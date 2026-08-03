def _signup_payload(email: str) -> dict:
    return {
        "account_name": "Audit Test Co",
        "account_type": "individual",
        "email": email,
        "password": "supersecret123",
    }


def _signup_and_login(client, email: str) -> str:
    client.post("/auth/signup", json=_signup_payload(email))
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_signup_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    signup_response = client.post("/auth/signup", json=_signup_payload("audit1@example.com"))
    account_id = signup_response.json()["account_id"]

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "account.signup", AuditEvent.target == f"account:{account_id}")
        .all()
    )
    assert len(events) == 1
    assert events[0].after_hash is not None


def test_login_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    token = _signup_and_login(client, "audit2@example.com")
    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = me_response.json()["id"]

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "user.login", AuditEvent.target == f"user:{user_id}")
        .all()
    )
    assert len(events) == 1


def test_customer_owner_cannot_list_audit_events(client):
    """Audit lists events across ALL accounts, so a customer - even an
    account owner - must never be able to view it. Staff-only."""
    token = _signup_and_login(client, "audit3@example.com")
    response = client.get("/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_staff_can_list_audit_events(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    _signup_and_login(client, "audit4@example.com")

    staff_service.create_staff(
        db_session, email="staffaudit1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    login_response = client.post(
        "/staff/login", json={"email": "staffaudit1@zoikolocal.com", "password": "staffpass123"}
    )
    staff_token = login_response.json()["access_token"]

    response = client.get("/audit/events", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200
    actions = [e["action"] for e in response.json()]
    assert "account.signup" in actions
    assert "user.login" in actions


def test_audit_events_require_authentication(client):
    response = client.get("/audit/events")
    assert response.status_code == 401


def test_owner_can_list_their_own_account_events(client):
    token = _signup_and_login(client, "audit5@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/audit/events/me", headers=headers)
    assert response.status_code == 200
    actions = [e["action"] for e in response.json()]
    assert "account.signup" in actions
    assert "user.login" in actions


def test_member_cannot_list_account_events(client):
    """Same reasoning as consent/compliance: an account's own audit trail
    is an Owner/Admin-level view, not something every Member can browse."""
    owner_token = _signup_and_login(client, "auditmemberowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "auditmembermember@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "auditmembermember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.get("/audit/events/me", headers={"Authorization": f"Bearer {member_token}"})
    assert response.status_code == 403


def test_account_audit_events_do_not_leak_other_accounts(client):
    token_a = _signup_and_login(client, "auditisolationA@example.com")
    token_b = _signup_and_login(client, "auditisolationB@example.com")

    response_a = client.get("/audit/events/me", headers={"Authorization": f"Bearer {token_a}"})
    response_b = client.get("/audit/events/me", headers={"Authorization": f"Bearer {token_b}"})

    a_ids = {e["id"] for e in response_a.json()}
    b_ids = {e["id"] for e in response_b.json()}
    assert a_ids.isdisjoint(b_ids)
