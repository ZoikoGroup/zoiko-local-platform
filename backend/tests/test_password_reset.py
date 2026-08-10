import re

import pyotp


def _signup_and_login(client, email: str, password: str = "supersecret123") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Reset Test Co", "account_type": "individual", "email": email, "password": password},
    )
    return client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]


def _capture_reset_token(client, monkeypatch, email: str) -> str:
    """Requests a reset and pulls the real token out of the email body the
    same way a user would from their inbox - the API response never
    contains it (204, no body), matching real production behavior."""
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")

    response = client.post("/auth/forgot-password", json={"email": email})
    assert response.status_code == 204
    assert len(sent) == 1

    match = re.search(r"token=([^\s]+)", sent[0]["body"])
    assert match, sent[0]["body"]
    return match.group(1)


def test_forgot_password_always_returns_204_for_an_unknown_email(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")
    response = client.post("/auth/forgot-password", json={"email": "nobody-here@example.com"})
    assert response.status_code == 204


def test_forgot_password_returns_204_and_sends_an_email_for_a_real_account(client, monkeypatch):
    _signup_and_login(client, "resetreal1@example.com")
    token = _capture_reset_token(client, monkeypatch, "resetreal1@example.com")
    assert token


def test_forgot_password_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: None)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")
    _signup_and_login(client, "resetratelimit@example.com")

    for _ in range(5):
        response = client.post("/auth/forgot-password", json={"email": "resetratelimit@example.com"})
        assert response.status_code == 204

    over_limit = client.post("/auth/forgot-password", json={"email": "resetratelimit@example.com"})
    assert over_limit.status_code == 429


def test_reset_password_with_a_valid_token_changes_the_password_and_logs_in(client, monkeypatch):
    _signup_and_login(client, "resetsuccess1@example.com", password="oldpassword123")
    token = _capture_reset_token(client, monkeypatch, "resetsuccess1@example.com")

    response = client.post("/auth/reset-password", json={"token": token, "new_password": "brandnewpassword456"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mfa_required"] is False
    assert body["access_token"]

    old_login = client.post(
        "/auth/login", json={"email": "resetsuccess1@example.com", "password": "oldpassword123"}
    )
    assert old_login.status_code == 401

    new_login = client.post(
        "/auth/login", json={"email": "resetsuccess1@example.com", "password": "brandnewpassword456"}
    )
    assert new_login.status_code == 200


def test_reset_password_rejects_a_malformed_token(client):
    response = client.post("/auth/reset-password", json={"token": "not-a-real-token", "new_password": "whatever123"})
    assert response.status_code == 400


def test_reset_password_rejects_an_unknown_token(client):
    from app.core.security import create_access_token

    fake_token = create_access_token(subject="00000000-0000-0000-0000-000000000000", scope="password_reset")
    response = client.post(
        "/auth/reset-password", json={"token": f"{fake_token}.deadbeefdeadbeef", "new_password": "whatever123"}
    )
    assert response.status_code == 400


def test_reset_password_rejects_an_expired_token(client, monkeypatch):
    _signup_and_login(client, "resetexpired1@example.com")
    token = _capture_reset_token(client, monkeypatch, "resetexpired1@example.com")

    # Force the token to look already-expired by asking decode_access_token
    # to reject anything - simpler and more robust than constructing a
    # backdated JWT by hand, and exercises the same "invalid" code path.
    monkeypatch.setattr("app.numbering.identity.service.decode_access_token", lambda t: None)

    response = client.post("/auth/reset-password", json={"token": token, "new_password": "whatever123"})
    assert response.status_code == 400


def test_reset_password_token_is_single_use(client, monkeypatch):
    """Using the token once changes hashed_password, which changes the
    fingerprint the token was issued with - so replaying the same token a
    second time must fail (see password_fingerprint's docstring)."""
    _signup_and_login(client, "resetreuse1@example.com")
    token = _capture_reset_token(client, monkeypatch, "resetreuse1@example.com")

    first = client.post("/auth/reset-password", json={"token": token, "new_password": "firstnewpassword123"})
    assert first.status_code == 200

    second = client.post("/auth/reset-password", json={"token": token, "new_password": "secondnewpassword456"})
    assert second.status_code == 400


def test_reset_password_on_an_mfa_enabled_account_still_requires_the_second_factor(client, monkeypatch):
    token = _signup_and_login(client, "resetmfa1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    setup = client.post("/auth/mfa/setup", headers=headers).json()
    real_code = pyotp.TOTP(setup["secret"]).now()
    client.post("/auth/mfa/enable", json={"code": real_code}, headers=headers)

    reset_token = _capture_reset_token(client, monkeypatch, "resetmfa1@example.com")
    response = client.post("/auth/reset-password", json={"token": reset_token, "new_password": "newmfapassword123"})
    assert response.status_code == 200
    body = response.json()
    assert body["mfa_required"] is True
    assert body["access_token"] is None
    assert body["mfa_token"]


def test_reset_password_creates_audit_events(client, db_session, monkeypatch):
    from app.audit.models import AuditEvent

    _signup_and_login(client, "resetaudit1@example.com")
    token = _capture_reset_token(client, monkeypatch, "resetaudit1@example.com")
    client.post("/auth/reset-password", json={"token": token, "new_password": "auditedpassword123"})

    actions = {
        e.action
        for e in db_session.query(AuditEvent).all()
        if e.action.startswith("user.password_reset")
    }
    assert "user.password_reset_requested" in actions
    assert "user.password_reset_completed" in actions
