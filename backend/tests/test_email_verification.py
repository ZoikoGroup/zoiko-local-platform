import re


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Verify Test Co", "account_type": "individual", "email": email, "password": "supersecret123"},
    )
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def _capture_verification_token(sent: list) -> str:
    match = re.search(r"token=([^\s]+)", sent[-1]["body"])
    assert match, sent[-1]["body"]
    return match.group(1)


def test_signup_sends_a_verification_email_and_starts_unverified(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")

    signup = client.post(
        "/auth/signup",
        json={"account_name": "Verify Test Co", "account_type": "individual", "email": "unverified1@example.com", "password": "supersecret123"},
    )
    assert signup.status_code == 201
    assert signup.json()["email_verified"] is False
    # Signup also sends two unrelated emails (notify_account_activated's
    # welcome, notify_organization_created's onboarding nudge) - this only
    # asserts the verification one is among them, not that it's the only
    # one sent.
    assert len(sent) == 3
    assert sent[-1]["subject"] == "Verify your email for Zoiko Local"
    token = _capture_verification_token(sent)
    assert token


def test_verify_email_with_a_valid_token_marks_the_account_verified(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")

    token = _signup_and_login(client, "verifysuccess1@example.com")
    verify_token = _capture_verification_token(sent)

    response = client.post("/auth/verify-email", json={"token": verify_token})
    assert response.status_code == 200, response.text
    assert response.json()["email_verified"] is True

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email_verified"] is True


def test_verify_email_rejects_a_malformed_token(client):
    response = client.post("/auth/verify-email", json={"token": "not-a-real-token"})
    assert response.status_code == 400


def test_verify_email_rejects_an_unknown_user(client):
    from app.core.security import create_access_token

    fake_token = create_access_token(subject="00000000-0000-0000-0000-000000000000", scope="email_verification")
    response = client.post("/auth/verify-email", json={"token": fake_token})
    assert response.status_code == 400


def test_verify_email_rejects_a_token_with_the_wrong_scope(client, monkeypatch):
    """A password-reset token (or any other scope) must not double as a
    verification token - decode_access_token succeeding isn't enough, the
    scope claim must match exactly."""
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")
    _signup_and_login(client, "wrongscope1@example.com")

    client.post("/auth/forgot-password", json={"email": "wrongscope1@example.com"})
    reset_email = next(s for s in sent if s["subject"].startswith("Reset your"))
    reset_token = re.search(r"token=([^\s]+)", reset_email["body"]).group(1).rsplit(".", 1)[0]  # strip fingerprint

    response = client.post("/auth/verify-email", json={"token": reset_token})
    assert response.status_code == 400


def test_verify_email_is_idempotent_on_replay(client, monkeypatch):
    """A customer double-clicking (or re-opening) the same link after
    already verifying should see success, not a confusing error."""
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")

    _signup_and_login(client, "verifyreplay1@example.com")
    verify_token = _capture_verification_token(sent)

    first = client.post("/auth/verify-email", json={"token": verify_token})
    assert first.status_code == 200
    second = client.post("/auth/verify-email", json={"token": verify_token})
    assert second.status_code == 200
    assert second.json()["email_verified"] is True


def test_resend_verification_always_returns_204(client, monkeypatch):
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: None)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")

    unknown = client.post("/auth/resend-verification", json={"email": "nobody-here@example.com"})
    assert unknown.status_code == 204

    _signup_and_login(client, "resendreal1@example.com")
    known = client.post("/auth/resend-verification", json={"email": "resendreal1@example.com"})
    assert known.status_code == 204


def test_resend_verification_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: None)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")
    _signup_and_login(client, "resendratelimit1@example.com")

    for _ in range(5):
        response = client.post("/auth/resend-verification", json={"email": "resendratelimit1@example.com"})
        assert response.status_code == 204

    over_limit = client.post("/auth/resend-verification", json={"email": "resendratelimit1@example.com"})
    assert over_limit.status_code == 429


def test_resend_verification_is_a_no_op_for_an_already_verified_account(client, monkeypatch):
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")

    _signup_and_login(client, "resendverified1@example.com")
    verify_token = _capture_verification_token(sent)
    client.post("/auth/verify-email", json={"token": verify_token})

    before = len(sent)
    client.post("/auth/resend-verification", json={"email": "resendverified1@example.com"})
    assert len(sent) == before  # no second email sent


def test_google_signup_is_verified_immediately(client, monkeypatch):
    """Google already proved ownership of the address via its own
    id_token email_verified claim - no separate verification round-trip
    should be required."""
    monkeypatch.setattr(
        "app.numbering.identity.routes.verify_google_id_token",
        lambda credential: {"email": "googleverified1@example.com", "email_verified": True, "name": "Google User"},
    )
    response = client.post("/auth/google", json={"credential": "fake-credential"})
    assert response.status_code == 200
    token = response.json()["access_token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email_verified"] is True


def test_number_purchase_requires_email_verification(client, monkeypatch):
    """The actual enforcement point (Production Readiness Standard doc
    §5's "Identity" trial-abuse control) - an unverified account can look
    up numbers but can't buy one."""
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_configured")
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": "PN_fake_sid", "phone_number": e164, "capabilities": {}},
    )

    token = _signup_and_login(client, "buyunverified1@example.com")
    # Captured right after signup - compliance/billing calls below send
    # their own notifications, so sent[-1] would otherwise no longer be
    # the verification email by the time this test reaches for it.
    verify_token = _capture_verification_token(sent)
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers)
    client.put("/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"}, headers=headers)

    client.post("/numbers/reserve", json={"e164": "+15550001111", "country": "US"}, headers=headers)
    blocked = client.post("/numbers/purchase", json={"e164": "+15550001111"}, headers=headers)
    assert blocked.status_code == 403
    assert "verify" in blocked.json()["detail"].lower()

    client.post("/auth/verify-email", json={"token": verify_token})

    allowed = client.post("/numbers/purchase", json={"e164": "+15550001111"}, headers=headers)
    assert allowed.status_code == 200, allowed.text
