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

    def _fake_rate_usage_in_zoikonex(db, sub, usage_event, **kwargs):
        return {"rated_charge_id": "zn-rated-charge-test", "evidence_id": "zn-evidence-test"}

    def _fake_register_plan_in_catalog(db, plan, **kwargs):
        plan.zoikonex_product_id = plan.zoikonex_product_id or "zn-product-test"
        plan.zoikonex_offer_id = plan.zoikonex_offer_id or "zn-offer-test"
        plan.zoikonex_price_rule_id = plan.zoikonex_price_rule_id or "zn-price-rule-test"
        return {
            "product_id": plan.zoikonex_product_id, "offer_id": plan.zoikonex_offer_id,
            "price_rule_id": plan.zoikonex_price_rule_id,
        }

    def _fake_open_bill_cycle(sub):
        return {"bill_cycle_id": "zn-bill-cycle-test", "status": "OPEN"}

    def _fake_close_bill_cycle(bill_cycle_id):
        return {"status": "CLOSED"}

    def _fake_create_invoice(sub, bill_cycle_id, **kwargs):
        return {"invoice_id": "zn-invoice-test", "status": "DRAFT"}

    def _fake_get_invoice(invoice_id):
        # Always "DRAFT" - every test starts a fresh account/invoice, so
        # run_billing_cycle's live-status check (see get_invoice's docstring
        # on why create_invoice's own return value can't be trusted here)
        # always takes the "first run this period" branch.
        return {"invoice_id": invoice_id, "status": "DRAFT", "total_minor_units": 0}

    def _fake_add_invoice_line_item(invoice_id, **kwargs):
        return {"line_item_id": "zn-line-item-test"}

    def _fake_issue_invoice(invoice_id):
        return {"status": "ISSUED", "total_minor_units": 1999}

    def _fake_determine_tax_for_invoice_line(**kwargs):
        return {"tax_decision_id": "zn-tax-decision-test", "tax_amount_minor_units": 0}

    def _fake_create_credit_note(invoice_id, **kwargs):
        return {"credit_note_id": "zn-credit-note-test", "status": "ISSUED", "amount_minor_units": kwargs.get("amount_minor_units")}

    def _fake_create_debit_note(invoice_id, **kwargs):
        return {"debit_note_id": "zn-debit-note-test", "status": "ISSUED", "amount_minor_units": kwargs.get("amount_minor_units")}

    def _fake_create_payment_intent(sub, invoice_id, **kwargs):
        return {"payment_intent_id": "zn-payment-intent-test", "status": "CREATED"}

    def _fake_authorise_payment_intent(payment_intent_id):
        return {"status": "AUTHORISED"}

    def _fake_capture_payment_intent(payment_intent_id):
        return {"status": "CAPTURED"}

    def _fake_create_refund(payment_intent_id, **kwargs):
        return {"refund_id": "zn-refund-test", "status": "REFUNDED"}

    monkeypatch.setattr(billing_service.zoikonex_adapter, "sync_subscription", _fake_sync_subscription)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "sync_usage_event", _fake_sync_usage_event)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "rate_usage_in_zoikonex", _fake_rate_usage_in_zoikonex)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "register_plan_in_catalog", _fake_register_plan_in_catalog)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "open_bill_cycle", _fake_open_bill_cycle)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "close_bill_cycle", _fake_close_bill_cycle)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "create_invoice", _fake_create_invoice)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "get_invoice", _fake_get_invoice)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "add_invoice_line_item", _fake_add_invoice_line_item)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "issue_invoice", _fake_issue_invoice)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "determine_tax_for_invoice_line", _fake_determine_tax_for_invoice_line)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "create_credit_note", _fake_create_credit_note)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "create_debit_note", _fake_create_debit_note)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "create_payment_intent", _fake_create_payment_intent)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "authorise_payment_intent", _fake_authorise_payment_intent)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "capture_payment_intent", _fake_capture_payment_intent)
    monkeypatch.setattr(billing_service.zoikonex_adapter, "create_refund", _fake_create_refund)
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


@pytest.fixture(autouse=True)
def _test_markets_stay_paid_open(db_session):
    """2026-08-19: all 8 seeded countries were pulled back from PAID_OPEN to
    CONTROLLED_BETA in the real dev DB (migration 877c1c4434e7 - no real
    legal/tax review was ever recorded for them; a real product decision,
    not a test-only concern - real customers should not be able to buy
    numbers there until someone actually reviews each market).

    Tried flagging test accounts is_test=True first (CONTROLLED_BETA's own
    existing carve-out for that flag) - reverted, since a second, unrelated
    gate (create_number_purchase_checkout_session's TestAccountRestrictedError)
    specifically blocks is_test accounts from checkout, breaking every
    real-commercial-account checkout test instead of fixing the market gate.

    This is the correct fix: reset market_status back to PAID_OPEN for the
    8 seeded countries inside THIS TEST'S OWN db_session transaction only -
    same rollback-at-teardown boundary as every other test-time DB change,
    so the real dev DB / real browser session stays on the pulled-back
    CONTROLLED_BETA state the product decision requires. Tests that
    specifically exercise CLOSED/SUSPENDED/CONTROLLED_BETA gating for a
    non-is_test account already create their own dedicated test countries
    (M1, M2, M3...) and are unaffected."""
    from app.numbering.numbers.models import MarketActivationStatus, SupportedCountry

    db_session.query(SupportedCountry).filter(
        SupportedCountry.code.in_(["US", "CA", "GB", "AU", "DE", "FR", "IN", "SG"])
    ).update({"market_status": MarketActivationStatus.PAID_OPEN}, synchronize_session=False)
    db_session.commit()
    yield


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
