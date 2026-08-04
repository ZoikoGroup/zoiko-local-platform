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


def test_me_returns_current_user_with_valid_token(client):
    client.post("/auth/signup", json=_signup_payload("me@example.com"))
    login_response = client.post(
        "/auth/login", json={"email": "me@example.com", "password": "supersecret123"}
    )
    token = login_response.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["email"] == "me@example.com"


def test_me_rejects_missing_token(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_rejects_invalid_token(client):
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_set_phone_number_requires_auth(client):
    response = client.put("/auth/me/phone", json={"phone_number": "+15551234567"})
    assert response.status_code == 401


def test_set_phone_number_saves_and_is_reflected_in_me(client):
    client.post("/auth/signup", json=_signup_payload("phoneuser@example.com"))
    token = client.post(
        "/auth/login", json={"email": "phoneuser@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put("/auth/me/phone", json={"phone_number": "+15551234567"}, headers=headers)
    assert response.status_code == 200
    assert response.json()["phone_number"] == "+15551234567"

    me = client.get("/auth/me", headers=headers)
    assert me.json()["phone_number"] == "+15551234567"


def test_set_phone_number_can_clear_it(client):
    client.post("/auth/signup", json=_signup_payload("phoneclear@example.com"))
    token = client.post(
        "/auth/login", json={"email": "phoneclear@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.put("/auth/me/phone", json={"phone_number": "+15551234567"}, headers=headers)
    response = client.put("/auth/me/phone", json={"phone_number": None}, headers=headers)
    assert response.status_code == 200
    assert response.json()["phone_number"] is None
