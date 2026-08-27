"""
Self-hosted error monitoring (Roadmap Month 5 launch-readiness gate) -
app.core.error_logging.ErrorLoggingMiddleware + app.observability.
"""

import pytest

from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Error Monitoring Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _create_and_login_staff(db_session, client, email: str) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def test_every_response_gets_a_request_id_header(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers.get("X-Request-ID")


def test_an_unhandled_exception_is_logged_before_propagating(client, db_session, monkeypatch):
    """A genuinely unexpected bug (not one of the already-handled provider/DB
    failure types) must still get an error_events row before the framework
    deals with it. Starlette's TestClient re-raises server exceptions to the
    calling test by default (raise_server_exceptions=True) rather than
    turning them into a 500 response - a real client would just see a plain
    500 with nothing leaked, same as main.py's DBAPIError handler already
    proves for the DB-outage case in test_chaos.py."""
    from app.observability.models import ErrorEvent

    token, account_id = _signup_and_login(client, "errormonunhandled@example.com")

    # Let the FIRST query through (get_current_user's own auth lookup, which
    # is what populates request.state.account_id) and only break the route's
    # own query after that - otherwise auth itself would fail first and
    # account_id would never get attached to this request at all.
    original_query = db_session.query
    call_count = {"n": 0}

    def _raise_after_first_call(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return original_query(*args, **kwargs)
        raise RuntimeError("something truly unexpected broke")

    monkeypatch.setattr(db_session, "query", _raise_after_first_call)

    with pytest.raises(RuntimeError, match="something truly unexpected broke"):
        client.get("/numbers", headers={"Authorization": f"Bearer {token}"})

    from app.core.database import SessionLocal

    fresh_db = SessionLocal()
    try:
        event = (
            fresh_db.query(ErrorEvent)
            .filter(ErrorEvent.path == "/numbers", ErrorEvent.exception_type == "RuntimeError")
            .order_by(ErrorEvent.created_at.desc())
            .first()
        )
        assert event is not None
        assert event.status_code == 500
        assert event.account_id == account_id
        assert "something truly unexpected broke" in event.exception_message
        fresh_db.delete(event)
        fresh_db.commit()
    finally:
        fresh_db.close()


def test_a_handled_5xx_response_is_also_logged(client, db_session, monkeypatch):
    """Not just genuinely-unhandled crashes - our own explicit
    raise HTTPException(502, ...) after a caught provider failure is worth
    the same visibility, since it's still a real production failure."""
    from app.billing.models import SubscriptionStatus
    from app.billing.service import get_or_create_subscription
    from app.observability.models import ErrorEvent

    monkeypatch.setattr("app.integrations.video.livekit.settings.livekit_url", "")
    token, account_id = _signup_and_login(client, "errormonhandled@example.com")

    # app.core.deps.require_paid_or_read_only blocks write actions (POST
    # /media/video/rooms) for a TRIALING account; this test is about error
    # logging, not trial-gating, so clear it directly (same pattern as
    # test_risk.py's _clear_billing_trial_gate).
    sub = get_or_create_subscription(db_session, account_id)
    sub.status = SubscriptionStatus.ACTIVE
    sub.trial_ends_at = None
    db_session.commit()

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 502
    request_id = response.headers["X-Request-ID"]

    from app.core.database import SessionLocal

    fresh_db = SessionLocal()
    try:
        event = fresh_db.query(ErrorEvent).filter(ErrorEvent.request_id == request_id).first()
        assert event is not None
        assert event.status_code == 502
        assert event.exception_type is None  # no traceback for a handled HTTPException
        fresh_db.delete(event)
        fresh_db.commit()
    finally:
        fresh_db.close()


def test_a_normal_request_does_not_create_an_error_event(client):
    from app.observability.models import ErrorEvent

    token, _ = _signup_and_login(client, "errormonnormal@example.com")
    response = client.get("/numbers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    request_id = response.headers["X-Request-ID"]

    from app.core.database import SessionLocal

    fresh_db = SessionLocal()
    try:
        assert fresh_db.query(ErrorEvent).filter(ErrorEvent.request_id == request_id).first() is None
    finally:
        fresh_db.close()


def test_a_client_error_does_not_create_an_error_event(client):
    """404/403/422 etc. are normal expected outcomes, not production
    failures - only >=500 belongs in error monitoring."""
    from app.observability.models import ErrorEvent

    response = client.get("/media/video/rooms")  # 401, no auth
    assert response.status_code == 401
    request_id = response.headers["X-Request-ID"]

    from app.core.database import SessionLocal

    fresh_db = SessionLocal()
    try:
        assert fresh_db.query(ErrorEvent).filter(ErrorEvent.request_id == request_id).first() is None
    finally:
        fresh_db.close()


def test_list_errors_requires_staff_auth(client):
    response = client.get("/ops/errors")
    assert response.status_code == 401


def test_customer_cannot_list_errors(client):
    token, _ = _signup_and_login(client, "errormoncustomer@example.com")
    response = client.get("/ops/errors", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_staff_can_list_and_view_error_detail(client, db_session):
    from app.observability.service import record_error_event

    record_error_event(
        request_id="req-test-1", method="GET", path="/numbers", status_code=500,
        exception=RuntimeError("boom for staff view test"),
    )

    staff_token = _create_and_login_staff(db_session, client, "errormonstaff1@zoikolocal.com")
    headers = {"Authorization": f"Bearer {staff_token}"}

    list_response = client.get("/ops/errors", headers=headers)
    assert list_response.status_code == 200
    matching = next((e for e in list_response.json() if e["request_id"] == "req-test-1"), None)
    assert matching is not None
    assert matching["exception_type"] == "RuntimeError"
    assert "traceback" not in matching  # list view omits the full traceback

    detail_response = client.get(f"/ops/errors/{matching['id']}", headers=headers)
    assert detail_response.status_code == 200
    assert "boom for staff view test" in detail_response.json()["traceback"]

    # cleanup - written via a fresh session outside any test transaction
    from app.core.database import SessionLocal
    from app.observability.models import ErrorEvent

    fresh_db = SessionLocal()
    try:
        fresh_db.query(ErrorEvent).filter(ErrorEvent.request_id == "req-test-1").delete()
        fresh_db.commit()
    finally:
        fresh_db.close()


def test_error_summary_groups_by_type_path_and_status(client, db_session):
    from app.observability.service import record_error_event

    for _ in range(3):
        record_error_event(
            request_id=f"req-summary-{_}", method="GET", path="/media/video/rooms", status_code=502,
        )

    staff_token = _create_and_login_staff(db_session, client, "errormonstaff2@zoikolocal.com")
    response = client.get("/ops/errors/summary", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200
    matching = next(
        (r for r in response.json() if r["path"] == "/media/video/rooms" and r["status_code"] == 502), None
    )
    assert matching is not None
    assert matching["count"] >= 3

    from app.core.database import SessionLocal
    from app.observability.models import ErrorEvent

    fresh_db = SessionLocal()
    try:
        fresh_db.query(ErrorEvent).filter(ErrorEvent.path == "/media/video/rooms").delete()
        fresh_db.commit()
    finally:
        fresh_db.close()


def test_error_detail_returns_404_for_an_unknown_id(client, db_session):
    staff_token = _create_and_login_staff(db_session, client, "errormonstaff3@zoikolocal.com")
    response = client.get(
        "/ops/errors/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 404
