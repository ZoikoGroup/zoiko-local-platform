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
