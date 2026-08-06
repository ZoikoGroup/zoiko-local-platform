def _signup_and_login(client, email: str, account_name: str = "CRM Test Co") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": account_name, "account_type": "business", "email": email, "password": "supersecret123"},
    )
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def test_owner_can_connect_a_crm(client):
    token = _signup_and_login(client, "crm-owner1@example.com")
    response = client.post(
        "/crm/connect", json={"provider": "hubspot"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["provider"] == "hubspot"
    assert "Mock Hubspot" in body["external_account_label"]


def test_connect_rejects_an_unknown_provider(client):
    token = _signup_and_login(client, "crm-owner2@example.com")
    response = client.post(
        "/crm/connect", json={"provider": "not-a-real-crm"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


def test_cannot_connect_twice_without_disconnecting(client):
    token = _signup_and_login(client, "crm-owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/crm/connect", json={"provider": "hubspot"}, headers=headers)
    response = client.post("/crm/connect", json={"provider": "salesforce"}, headers=headers)
    assert response.status_code == 409


def test_member_cannot_connect_a_crm(client):
    owner_token = _signup_and_login(client, "crm-owner4@example.com")
    client.post(
        "/team/members",
        json={"email": "crm-member4@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    member_token = client.post(
        "/auth/login", json={"email": "crm-member4@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/crm/connect", json={"provider": "hubspot"}, headers={"Authorization": f"Bearer {member_token}"}
    )
    assert response.status_code == 403


def test_disconnect_then_reconnect_with_a_different_provider(client):
    token = _signup_and_login(client, "crm-owner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/crm/connect", json={"provider": "hubspot"}, headers=headers)

    disconnect_response = client.post("/crm/disconnect", headers=headers)
    assert disconnect_response.status_code == 204

    reconnect_response = client.post("/crm/connect", json={"provider": "pipedrive"}, headers=headers)
    assert reconnect_response.status_code == 201
    assert reconnect_response.json()["provider"] == "pipedrive"


def test_disconnect_without_a_connection_returns_404(client):
    token = _signup_and_login(client, "crm-owner6@example.com")
    response = client.post("/crm/disconnect", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_get_connection_is_null_when_none_exists(client):
    token = _signup_and_login(client, "crm-owner7@example.com")
    response = client.get("/crm/connection", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() is None


def test_creating_a_contact_syncs_to_a_connected_crm(client):
    token = _signup_and_login(client, "crm-contact1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/crm/connect", json={"provider": "hubspot"}, headers=headers)

    client.post(
        "/contacts", json={"name": "Jane Doe", "phone_number": "+15551234567"}, headers=headers,
    )

    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert len(sync_log) == 1
    assert sync_log[0]["event_type"] == "contact_sync"
    assert sync_log[0]["payload"]["name"] == "Jane Doe"


def test_creating_a_contact_does_not_sync_without_a_connection(client):
    token = _signup_and_login(client, "crm-contact2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/contacts", json={"name": "No Sync", "phone_number": "+15559998888"}, headers=headers,
    )

    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert sync_log == []


def test_a_notification_worthy_event_syncs_a_crm_activity_when_connected(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token = _signup_and_login(client, "crm-activity1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/crm/connect", json={"provider": "hubspot"}, headers=headers)

    me = client.get("/auth/me", headers=headers).json()

    from app.notifications.service import send_notification

    send_notification(
        db_session, event_name="number.activated", account_id=me["account_id"],
        recipient_email="crm-activity1@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "CRM Test Co", "user_display_name": "crm-activity1@example.com",
        },
    )

    sync_log = client.get("/crm/sync-log", headers=headers).json()
    activity_events = [e for e in sync_log if e["event_type"] == "activity_sync"]
    assert len(activity_events) == 1
    assert activity_events[0]["payload"]["event_type"] == "number.activated"


def test_sync_log_is_scoped_to_the_callers_account(client):
    token_a = _signup_and_login(client, "crm-scope-a@example.com", "Scope A Co")
    token_b = _signup_and_login(client, "crm-scope-b@example.com", "Scope B Co")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    client.post("/crm/connect", json={"provider": "hubspot"}, headers=headers_a)
    client.post("/contacts", json={"name": "A Contact", "phone_number": "+15551110000"}, headers=headers_a)

    assert len(client.get("/crm/sync-log", headers=headers_a).json()) == 1
    assert client.get("/crm/sync-log", headers=headers_b).json() == []
