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


def test_owner_can_list_audit_events(client):
    token = _signup_and_login(client, "audit3@example.com")
    response = client.get("/audit/events", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    actions = [e["action"] for e in response.json()]
    assert "account.signup" in actions
    assert "user.login" in actions


def test_audit_events_require_authentication(client):
    response = client.get("/audit/events")
    assert response.status_code == 401
