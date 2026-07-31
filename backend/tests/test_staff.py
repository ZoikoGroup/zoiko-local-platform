from app.staff import service as staff_service


def _create_staff(db_session, email: str, password: str = "staffpass123"):
    return staff_service.create_staff(db_session, email=email, password=password)


def test_staff_login_succeeds_with_correct_credentials(client, db_session):
    _create_staff(db_session, "staff1@zoikolocal.com")
    response = client.post(
        "/staff/login", json={"email": "staff1@zoikolocal.com", "password": "staffpass123"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_staff_login_fails_with_wrong_password(client, db_session):
    _create_staff(db_session, "staff2@zoikolocal.com")
    response = client.post(
        "/staff/login", json={"email": "staff2@zoikolocal.com", "password": "wrong"}
    )
    assert response.status_code == 401


def test_there_is_no_public_staff_signup_endpoint(client):
    response = client.post(
        "/staff/signup", json={"email": "hacker@example.com", "password": "whatever123"}
    )
    assert response.status_code == 404


def test_customer_token_cannot_be_used_as_a_staff_token(client):
    """A customer logging in must never be treated as staff, even if they
    happen to be an account Owner."""
    client.post(
        "/auth/signup",
        json={
            "account_name": "Not Staff Co",
            "account_type": "individual",
            "email": "notstaff@example.com",
            "password": "supersecret123",
        },
    )
    login_response = client.post(
        "/auth/login", json={"email": "notstaff@example.com", "password": "supersecret123"}
    )
    customer_token = login_response.json()["access_token"]

    response = client.get("/audit/events", headers={"Authorization": f"Bearer {customer_token}"})
    assert response.status_code == 401


def test_staff_token_cannot_be_used_as_a_customer_token(client, db_session):
    """Symmetric check: a staff login must never work on customer-only
    endpoints either."""
    _create_staff(db_session, "staff3@zoikolocal.com")
    login_response = client.post(
        "/staff/login", json={"email": "staff3@zoikolocal.com", "password": "staffpass123"}
    )
    staff_token = login_response.json()["access_token"]

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 401
