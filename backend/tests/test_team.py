import logging


def _signup_and_login(client, email: str, account_name: str = "Team Test Co") -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": account_name,
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_owner_can_add_a_team_member(client):
    owner_token = _signup_and_login(client, "owner1@example.com")
    response = client.post(
        "/team/members",
        json={"email": "member1@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "member"


def test_adding_a_member_notifies_them_by_email(client, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token = _signup_and_login(client, "notifyowner@example.com", account_name="Notify Test Co")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        client.post(
            "/team/members",
            json={"email": "notifymember@example.com", "password": "supersecret123", "role": "member"},
            headers={"Authorization": f"Bearer {owner_token}"},
        )
    assert any(
        "notifymember@example.com" in record.message and "Notify Test Co" in record.message
        for record in caplog.records
    )


def test_new_member_belongs_to_the_same_account_as_the_owner(client):
    owner_token = _signup_and_login(client, "owner2@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {owner_token}"}).json()

    add_response = client.post(
        "/team/members",
        json={"email": "member2@example.com", "password": "supersecret123", "role": "admin"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert add_response.json()["account_id"] == me["account_id"]


def test_new_member_can_log_in_with_their_own_password(client):
    owner_token = _signup_and_login(client, "owner3@example.com")
    client.post(
        "/team/members",
        json={"email": "member3@example.com", "password": "membersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    login_response = client.post(
        "/auth/login", json={"email": "member3@example.com", "password": "membersecret123"}
    )
    assert login_response.status_code == 200


def test_cannot_add_a_second_owner(client):
    owner_token = _signup_and_login(client, "owner4@example.com")
    response = client.post(
        "/team/members",
        json={"email": "wannabeowner@example.com", "password": "supersecret123", "role": "owner"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 400


def test_member_role_cannot_add_team_members(client):
    owner_token = _signup_and_login(client, "owner5@example.com")
    client.post(
        "/team/members",
        json={"email": "member5@example.com", "password": "membersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    member_token = client.post(
        "/auth/login", json={"email": "member5@example.com", "password": "membersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/team/members",
        json={"email": "someoneelse@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_list_team_members_includes_owner_and_added_members(client):
    owner_token = _signup_and_login(client, "owner6@example.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "member6@example.com", "password": "supersecret123", "role": "member"},
        headers=headers,
    )

    response = client.get("/team/members", headers=headers)
    assert response.status_code == 200
    emails = [m["email"] for m in response.json()]
    assert "owner6@example.com" in emails
    assert "member6@example.com" in emails


def test_owner_can_remove_a_team_member(client):
    owner_token = _signup_and_login(client, "owner7@example.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    added = client.post(
        "/team/members",
        json={"email": "member7@example.com", "password": "supersecret123", "role": "member"},
        headers=headers,
    ).json()

    delete_response = client.delete(f"/team/members/{added['id']}", headers=headers)
    assert delete_response.status_code == 204

    list_response = client.get("/team/members", headers=headers)
    emails = [m["email"] for m in list_response.json()]
    assert "member7@example.com" not in emails


def test_cannot_remove_the_account_owner(client):
    owner_token = _signup_and_login(client, "owner8@example.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    me = client.get("/auth/me", headers=headers).json()

    response = client.delete(f"/team/members/{me['id']}", headers=headers)
    assert response.status_code == 400


def test_cannot_remove_a_member_from_a_different_account(client):
    owner_a_token = _signup_and_login(client, "ownerA@example.com", "Account A")
    added = client.post(
        "/team/members",
        json={"email": "memberA@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_a_token}"},
    ).json()

    owner_b_token = _signup_and_login(client, "ownerB@example.com", "Account B")
    response = client.delete(
        f"/team/members/{added['id']}", headers={"Authorization": f"Bearer {owner_b_token}"}
    )
    assert response.status_code == 400


def test_adding_a_member_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    owner_token = _signup_and_login(client, "owner9@example.com")
    added = client.post(
        "/team/members",
        json={"email": "member9@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "team.member_added", AuditEvent.target == f"user:{added['id']}")
        .all()
    )
    assert len(events) == 1
