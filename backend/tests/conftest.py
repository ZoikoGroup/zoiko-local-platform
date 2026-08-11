import asyncio
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.database import Base, engine, get_db
from app.core.rate_limit import limiter
from app.main import app

if sys.platform == "win32":
    # aiohttp (used by the LiveKit SDK) can hang on Windows' default
    # ProactorEventLoop when many event loops are created/torn down in
    # sequence (once per test, via TestClient) — the selector policy doesn't
    # have this issue.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(scope="session", autouse=True)
def create_schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """The login rate limiter's storage is process-wide, not per-request -
    without this, hundreds of tests logging in from the same TestClient IP
    would trip each other's limits well within the 5/minute window. Real
    rate limiting itself is verified in its own dedicated test, which
    exhausts the limit deliberately rather than relying on this leaking
    from an unrelated test."""
    limiter.reset()
    yield


@pytest.fixture(autouse=True)
def reset_circuit_breakers():
    """Each integrations/<category>/ Provider Gateway module holds its own
    process-wide CircuitBreaker singleton (app.integrations._shared.
    circuit_breaker.CircuitBreaker) - same "leaks across tests" problem
    reset_rate_limiter above already documents for the login limiter. A
    failover test that deliberately trips a breaker open (3+ simulated
    failures) would otherwise leave it open for its full 30s reset_timeout
    for every later test in the same process, e.g. making an unrelated
    /media/voice/outbound call in a risk/velocity test come back 503
    ("circuit open") instead of exercising the behavior that test actually
    checks."""
    from app.integrations.kyc.stripe_identity import _breaker as kyc_breaker
    from app.integrations.llm.groq import _breaker as llm_breaker
    from app.integrations.notifications.email import _breaker as email_breaker
    from app.integrations.notifications.webpush import _breaker as webpush_breaker
    from app.integrations.storage.s3 import _breaker as storage_breaker
    from app.integrations.telecom.twilio import _breaker as telecom_breaker
    from app.integrations.transcription.groq import _breaker as transcription_breaker
    from app.integrations.video.livekit import _breaker as video_breaker

    for breaker in (
        kyc_breaker, llm_breaker, email_breaker, webpush_breaker,
        storage_breaker, telecom_breaker, transcription_breaker, video_breaker,
    ):
        breaker.record_success()
    yield


@pytest.fixture(autouse=True)
def mock_zoikonex_sync(monkeypatch):
    """app.integrations.billing.zoikonex is a real HTTP client against a
    self-hosted ZoikoNex backend (not a mock, unlike every other Provider
    Gateway's test posture) - get_or_create_subscription (called from
    dozens of unrelated tests via billing quota checks) would otherwise
    make a real network call to a ZoikoNex instance most environments
    running this suite don't have running. Same "mock the provider, don't
    hit the network" discipline as Twilio/Stripe elsewhere in this suite.
    Deliberately not real ZoikoNexError-raising behavior - tests that need
    to exercise ZoikoNex failure/success specifics (test_zoikonex_mock.py)
    override this per-test with their own monkeypatch.setattr call, which
    wins since it runs after this fixture in the same test."""
    from app.billing import service as billing_service

    def _fake_sync_subscription(db, sub, *, account_type):
        sub.zoikonex_party_id = sub.zoikonex_party_id or "zn-party-test"
        sub.zoikonex_customer_id = sub.zoikonex_customer_id or "zn-cust-test"
        sub.zoikonex_account_id = sub.zoikonex_account_id or "zn-acct-test"
        return {
            "party_id": sub.zoikonex_party_id,
            "customer_id": sub.zoikonex_customer_id,
            "account_id": sub.zoikonex_account_id,
        }

    def _fake_sync_usage_event(db, sub, usage_event_id, **kwargs):
        return {"zoikonex_ref": "zn-usage-test", "status": "NORMALISED"}

    monkeypatch.setattr(billing_service.zoikonex_adapter, "sync_subscription", _fake_sync_subscription)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "sync_usage_event", _fake_sync_usage_event)
    yield


@pytest.fixture(autouse=True)
def cleanup_error_events():
    """error_events rows are written via a deliberately independent DB
    session (see app.observability.service.record_error_event's docstring -
    it must survive even a broken/rolled-back request transaction), so
    they're NOT cleaned up by db_session's per-test rollback like everything
    else. Without this, every chaos/failure test that triggers a real 5xx
    would leave a permanent row in the shared dev database on every test
    run, forever."""
    yield
    from app.core.database import SessionLocal
    from app.observability.models import ErrorEvent

    db = SessionLocal()
    try:
        db.query(ErrorEvent).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def cleanup_provider_call_traces():
    """Same rationale as cleanup_error_events - record_provider_call_trace
    also uses an independent SessionLocal (see its docstring), so rows
    written by any test that exercises a traced Provider Gateway call
    escape the normal per-test transaction rollback."""
    yield
    from app.core.database import SessionLocal
    from app.observability.models import ProviderCallTrace

    db = SessionLocal()
    try:
        db.query(ProviderCallTrace).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture()
def db_session():
    """Each test runs inside its own transaction, rolled back at the end —
    keeps the dev database clean without needing a separate test database."""
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
