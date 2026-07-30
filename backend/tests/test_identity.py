def _signup_payload(email: str) -> dict:
    return {
        "account_name": "Test Co",
        "account_type": "individual",
        "email": email,
        "password": "supersecret123",
    }


def test_signup_creates_account_and_user(client):
    response = client.post("/auth/signup", json=_signup_payload("test@example.com"))
    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["role"] == "owner"
    assert data["account_id"]


def test_signup_duplicate_email_fails(client):
    payload = _signup_payload("dupe@example.com")
    client.post("/auth/signup", json=payload)
    response = client.post("/auth/signup", json=payload)
    assert response.status_code == 400


def test_login_success(client):
    client.post("/auth/signup", json=_signup_payload("login@example.com"))
    response = client.post(
        "/auth/login", json={"email": "login@example.com", "password": "supersecret123"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_login_wrong_password_fails(client):
    client.post("/auth/signup", json=_signup_payload("wrongpw@example.com"))
    response = client.post(
        "/auth/login", json={"email": "wrongpw@example.com", "password": "incorrect"}
    )
    assert response.status_code == 401


def test_login_unknown_email_fails(client):
    response = client.post(
        "/auth/login", json={"email": "doesnotexist@example.com", "password": "whatever"}
    )
    assert response.status_code == 401
