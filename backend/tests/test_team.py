import logging


def _signup_and_login(client, db_session, email: str, account_name: str = "Team Test Co") -> str:
    """Real gap fix (ZL-COM-ENT-001): adding a team member now requires the
    team.members.enabled entitlement (Business+ only - see
    app.billing.service.assert_entitlement in add_team_member) - a
    free_trial account no longer qualifies, unlike before this gate
    existed. Every test in this file exercises team-member add/remove
    directly, so this shared helper upgrades to Business rather than
    repeating that at each call site."""
    from app.billing import service as billing_service

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
    token = response.json()["access_token"]
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")
    return token


def test_owner_can_add_a_team_member(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner1@example.com")
    response = client.post(
        "/team/members",
        json={"email": "member1@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 201
    assert response.json()["role"] == "member"


def test_adding_a_member_notifies_them_by_email(client, db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token = _signup_and_login(client, db_session, "notifyowner@example.com", account_name="Notify Test Co")
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


def test_new_member_belongs_to_the_same_account_as_the_owner(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner2@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {owner_token}"}).json()

    add_response = client.post(
        "/team/members",
        json={"email": "member2@example.com", "password": "supersecret123", "role": "admin"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert add_response.json()["account_id"] == me["account_id"]


def test_new_member_can_log_in_with_their_own_password(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner3@example.com")
    client.post(
        "/team/members",
        json={"email": "member3@example.com", "password": "membersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )

    login_response = client.post(
        "/auth/login", json={"email": "member3@example.com", "password": "membersecret123"}
    )
    assert login_response.status_code == 200


def test_cannot_add_a_second_owner(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner4@example.com")
    response = client.post(
        "/team/members",
        json={"email": "wannabeowner@example.com", "password": "supersecret123", "role": "owner"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert response.status_code == 400


def test_member_role_cannot_add_team_members(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner5@example.com")
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


def test_free_trial_account_cannot_add_a_team_member(client, db_session):
    """ZL-COM-ENT-001 §7 matrix: team.members.enabled is Business+ only -
    a free_trial admin must be denied with a real entitlement code, not
    silently allowed the way it was before this gate existed. Signs up
    directly (bypassing this file's own _signup_and_login helper, which
    upgrades every account to Business) so the account stays on its
    default free_trial plan."""
    client.post(
        "/auth/signup",
        json={
            "account_name": "Free Trial Team Co", "account_type": "business",
            "email": "freetrialteamowner@example.com", "password": "supersecret123",
        },
    )
    token = client.post(
        "/auth/login", json={"email": "freetrialteamowner@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/team/members",
        json={"email": "freetrialteammate@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 402, response.text
    body = response.json()["detail"]
    assert body["code"] == "ENTITLEMENT_REQUIRED"
    assert body["entitlement"] == "team.enabled"
    assert body["current_plan"] == "free_trial"


def test_list_team_members_includes_owner_and_added_members(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner6@example.com")
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


def test_owner_can_remove_a_team_member(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner7@example.com")
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


def test_cannot_remove_the_account_owner(client, db_session):
    owner_token = _signup_and_login(client, db_session, "owner8@example.com")
    headers = {"Authorization": f"Bearer {owner_token}"}
    me = client.get("/auth/me", headers=headers).json()

    response = client.delete(f"/team/members/{me['id']}", headers=headers)
    assert response.status_code == 400


def test_cannot_remove_a_member_from_a_different_account(client, db_session):
    owner_a_token = _signup_and_login(client, db_session, "ownerA@example.com", "Account A")
    added = client.post(
        "/team/members",
        json={"email": "memberA@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_a_token}"},
    ).json()

    owner_b_token = _signup_and_login(client, db_session, "ownerB@example.com", "Account B")
    response = client.delete(
        f"/team/members/{added['id']}", headers={"Authorization": f"Bearer {owner_b_token}"}
    )
    assert response.status_code == 400


def test_adding_a_member_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    owner_token = _signup_and_login(client, db_session, "owner9@example.com")
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
