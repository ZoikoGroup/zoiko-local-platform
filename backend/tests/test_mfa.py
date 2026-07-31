import pyotp


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "MFA Test Co",
            "account_type": "individual",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_login_without_mfa_returns_a_token_directly(client):
    token = _signup_and_login(client, "nomfa@example.com")
    assert token


def test_setup_returns_a_real_totp_secret(client):
    token = _signup_and_login(client, "mfasetup@example.com")
    response = client.post("/auth/mfa/setup", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body["secret"]) >= 16
    assert body["otpauth_uri"].startswith("otpauth://totp/")


def test_enable_requires_a_valid_code(client):
    token = _signup_and_login(client, "mfaenable1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post("/auth/mfa/enable", json={"code": "000000"}, headers=headers)
    assert response.status_code == 400


def test_enable_succeeds_with_a_real_generated_code(client):
    token = _signup_and_login(client, "mfaenable2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()

    real_code = pyotp.TOTP(setup["secret"]).now()
    response = client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)
    assert response.status_code == 204


def test_login_after_enabling_mfa_requires_a_second_step(client):
    token = _signup_and_login(client, "mfalogin1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    real_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)

    login_response = client.post(
        "/auth/login", json={"email": "mfalogin1@example.com", "password": "supersecret123"}
    )
    body = login_response.json()
    assert body["mfa_required"] is True
    assert body["access_token"] is None
    assert body["mfa_token"]


def test_completing_mfa_login_with_correct_code_issues_a_real_token(client):
    token = _signup_and_login(client, "mfalogin2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    real_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)

    login_response = client.post(
        "/auth/login", json={"email": "mfalogin2@example.com", "password": "supersecret123"}
    )
    mfa_token = login_response.json()["mfa_token"]

    second_code = pyotp.TOTP(setup["secret"]).now()
    complete_response = client.post(
        "/auth/mfa/login", json={"mfa_token": mfa_token, "code": second_code}
    )
    assert complete_response.status_code == 200
    final_token = complete_response.json()["access_token"]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {final_token}"})
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "mfalogin2@example.com"


def test_completing_mfa_login_with_wrong_code_fails(client):
    token = _signup_and_login(client, "mfalogin3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    real_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)

    login_response = client.post(
        "/auth/login", json={"email": "mfalogin3@example.com", "password": "supersecret123"}
    )
    mfa_token = login_response.json()["mfa_token"]

    response = client.post("/auth/mfa/login", json={"mfa_token": mfa_token, "code": "000000"})
    assert response.status_code == 401


def test_a_regular_customer_token_cannot_be_used_to_complete_mfa_login(client):
    """The mfa_token has its own scope - a normal logged-in session token
    must not work here, otherwise MFA would be trivially bypassable."""
    token = _signup_and_login(client, "mfabypass@example.com")
    response = client.post("/auth/mfa/login", json={"mfa_token": token, "code": "000000"})
    assert response.status_code == 401


def test_disable_mfa_requires_a_valid_code(client):
    token = _signup_and_login(client, "mfadisable1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    real_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)

    response = client.post("/auth/mfa/disable", json={"code": "000000"}, headers=headers)
    assert response.status_code == 400


def test_disable_mfa_then_login_no_longer_requires_a_second_step(client):
    token = _signup_and_login(client, "mfadisable2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    enable_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": enable_code}, headers=headers)

    disable_code = pyotp.TOTP(setup["secret"]).now()
    disable_response = client.post(
        "/auth/mfa/disable", json={"code": disable_code}, headers=headers
    )
    assert disable_response.status_code == 204

    login_response = client.post(
        "/auth/login", json={"email": "mfadisable2@example.com", "password": "supersecret123"}
    )
    assert login_response.json()["mfa_required"] is False
    assert login_response.json()["access_token"]


def test_enabling_mfa_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    token = _signup_and_login(client, "mfaaudit1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    real_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)

    me = client.get("/auth/me", headers=headers).json()
    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "mfa.enabled", AuditEvent.target == f"user:{me['id']}")
        .all()
    )
    assert len(events) == 1
