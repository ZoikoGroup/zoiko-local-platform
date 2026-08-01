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


def test_list_accounts_requires_staff_auth(client):
    response = client.get("/staff/accounts")
    assert response.status_code == 401


def test_customer_cannot_list_accounts(client):
    client.post(
        "/auth/signup",
        json={
            "account_name": "Overview Test Co",
            "account_type": "business",
            "email": "overviewcustomer@example.com",
            "password": "supersecret123",
        },
    )
    login = client.post(
        "/auth/login", json={"email": "overviewcustomer@example.com", "password": "supersecret123"}
    )
    customer_token = login.json()["access_token"]

    response = client.get("/staff/accounts", headers={"Authorization": f"Bearer {customer_token}"})
    assert response.status_code == 401


def test_staff_can_list_accounts_with_owner_and_counts(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Overview Owner Co",
            "account_type": "business",
            "email": "overviewowner@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    owner_token = client.post(
        "/auth/login", json={"email": "overviewowner@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    client.post(
        "/team/members",
        json={"email": "overviewteammate@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    _create_staff(db_session, "staff4@zoikolocal.com")
    staff_token = client.post(
        "/staff/login", json={"email": "staff4@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get("/staff/accounts", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200
    match = next(a for a in response.json() if a["id"] == account_id)
    assert match["owner_email"] == "overviewowner@example.com"
    assert match["member_count"] == 2
    assert match["number_count"] == 0
