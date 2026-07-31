from app.numbering.identity import routes as identity_routes


def test_invalid_google_credential_is_rejected(client):
    """A garbage string is not a real Google-signed token - this
    genuinely exercises real verification, no mocking needed."""
    response = client.post("/auth/google", json={"credential": "not-a-real-token"})
    assert response.status_code == 401


def test_valid_google_credential_creates_a_new_account(client, db_session, monkeypatch):
    """We mock Google's own token verification (that's Google's code, not
    ours) to test OUR logic: does a verified Google login correctly
    create an account and issue a working JWT."""

    def fake_verify(_credential):
        return {"email": "newgoogleuser@example.com", "name": "New Google User"}

    monkeypatch.setattr(identity_routes, "verify_google_id_token", fake_verify)

    response = client.post("/auth/google", json={"credential": "fake-but-verified"})
    assert response.status_code == 200
    token = response.json()["access_token"]

    me_response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "newgoogleuser@example.com"
    assert me_response.json()["role"] == "owner"


def test_valid_google_credential_logs_into_existing_account(client, db_session, monkeypatch):
    """Signing up normally, then 'logging in with Google' using the same
    email, should log into the SAME account, not create a second one."""
    client.post(
        "/auth/signup",
        json={
            "account_name": "Existing Co",
            "account_type": "individual",
            "email": "existing@example.com",
            "password": "supersecret123",
        },
    )

    def fake_verify(_credential):
        return {"email": "existing@example.com", "name": "Existing User"}

    monkeypatch.setattr(identity_routes, "verify_google_id_token", fake_verify)

    response = client.post("/auth/google", json={"credential": "fake-but-verified"})
    assert response.status_code == 200

    from app.numbering.identity.models import User

    matching_users = db_session.query(User).filter(User.email == "existing@example.com").all()
    assert len(matching_users) == 1  # not duplicated


def test_google_only_account_cannot_log_in_with_a_password(client, db_session, monkeypatch):
    """A Google-only user has no password at all - /auth/login must
    reject them cleanly, not crash."""

    def fake_verify(_credential):
        return {"email": "googleonly@example.com", "name": "Google Only"}

    monkeypatch.setattr(identity_routes, "verify_google_id_token", fake_verify)
    client.post("/auth/google", json={"credential": "fake-but-verified"})

    response = client.post(
        "/auth/login", json={"email": "googleonly@example.com", "password": "anything"}
    )
    assert response.status_code == 401
