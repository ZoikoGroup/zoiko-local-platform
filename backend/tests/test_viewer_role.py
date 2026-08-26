"""Viewer/Auditor account role (Roadmap §2 Accounts) - full account-wide
read access, zero write access anywhere. See app.core.deps.require_writer
and UserRole.VIEWER's docstring for the architecture rationale."""

import logging


def _signup_and_login(client, email: str, account_type: str = "individual") -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Viewer Test Co",
            "account_type": account_type,
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    token = response.json()["access_token"]
    client.post(
        "/compliance/consent",
        json={"consent_type": "emergency_calling_acknowledged"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _add_viewer(client, db_session, owner_headers, email: str) -> str:
    from app.billing import service as billing_service

    owner_account_id = client.get("/auth/me", headers=owner_headers).json()["account_id"]
    # team.members.enabled is Business+ (ZL-COM-ENT-001) - a fresh signup's
    # default free_trial plan grants no team capability.
    billing_service.change_plan(db_session, owner_account_id, "business", actor="test-setup")

    add_response = client.post(
        "/team/members",
        json={"email": email, "password": "viewersecret123", "role": "viewer"},
        headers=owner_headers,
    )
    assert add_response.status_code == 201, add_response.text
    assert add_response.json()["role"] == "viewer"

    login_response = client.post("/auth/login", json={"email": email, "password": "viewersecret123"})
    return login_response.json()["access_token"]


def _stub_buy_number(monkeypatch):
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": "PN_fake_sid", "phone_number": e164, "capabilities": {}},
    )


def test_owner_can_add_a_viewer_team_member(client, db_session):
    owner_token = _signup_and_login(client, "viewerowner1@example.com")
    _add_viewer(client, db_session, {"Authorization": f"Bearer {owner_token}"}, "viewer1@example.com")


def test_viewer_can_list_numbers(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "viewerowner2@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post("/numbers/reserve", json={"e164": "+15550002222", "country": "US"}, headers=owner_headers)
    client.post("/numbers/purchase", json={"e164": "+15550002222"}, headers=owner_headers)

    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer2@example.com")
    response = client.get("/numbers", headers={"Authorization": f"Bearer {viewer_token}"})
    assert response.status_code == 200
    assert any(n["e164"] == "+15550002222" for n in response.json())


def test_viewer_cannot_list_team_members(client, db_session):
    """/team/members is require_admin-gated for every non-admin role, not
    just Viewer (a plain Member can't list it either) - this predates the
    Viewer role and is a pre-existing Admin-only restriction, not something
    require_writer controls."""
    owner_token = _signup_and_login(client, "viewerowner3@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer3@example.com")

    response = client.get("/team/members", headers={"Authorization": f"Bearer {viewer_token}"})
    assert response.status_code == 403


def test_viewer_cannot_suspend_a_number(client, db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "viewerowner4@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post("/numbers/reserve", json={"e164": "+15550003333", "country": "US"}, headers=owner_headers)
    client.post("/numbers/purchase", json={"e164": "+15550003333"}, headers=owner_headers)

    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer4@example.com")
    response = client.post(
        "/numbers/+15550003333/suspend", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Viewer role is read-only"


def test_viewer_cannot_reserve_a_number(client, db_session):
    owner_token = _signup_and_login(client, "viewerowner5@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer5@example.com")

    response = client.post(
        "/numbers/reserve",
        json={"e164": "+15550004444", "country": "US"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


def test_viewer_cannot_add_team_members(client, db_session):
    owner_token = _signup_and_login(client, "viewerowner6@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer6@example.com")

    response = client.post(
        "/team/members",
        json={"email": "sneakymember@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403


def test_viewer_cannot_mark_notification_read(client, db_session):
    owner_token = _signup_and_login(client, "viewerowner7@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer7@example.com")

    response = client.post(
        "/notifications/read-all", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert response.status_code == 403


def test_viewer_can_still_set_up_their_own_mfa(client, db_session):
    """MFA setup is a personal login/security setting, not business data -
    deliberately left on get_current_user, not require_writer."""
    owner_token = _signup_and_login(client, "viewerowner8@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer8@example.com")

    response = client.post(
        "/auth/mfa/setup", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert response.status_code == 200


def test_viewer_can_still_update_their_own_phone_number(client, db_session):
    owner_token = _signup_and_login(client, "viewerowner9@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    viewer_token = _add_viewer(client, db_session, owner_headers, "viewer9@example.com")

    response = client.put(
        "/auth/me/phone",
        json={"phone_number": "+15550009999"},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 200
