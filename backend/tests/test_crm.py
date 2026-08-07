from datetime import datetime, timedelta, timezone

from app.core.crypto import encrypt_secret
from app.crm.models import CrmConnection, CrmProvider


def _signup_and_login(client, email: str, account_name: str = "CRM Test Co") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": account_name, "account_type": "business", "email": email, "password": "supersecret123"},
    )
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def _insert_real_connection(db_session, account_id: str, provider: CrmProvider = CrmProvider.HUBSPOT) -> CrmConnection:
    """All three CrmProvider values are real OAuth now (see the mock module's
    docstring) - tests that only care about generic connection behavior
    (notifications, RBAC, scoping, disconnect) use this to get a connected
    account without going through a real provider's network calls."""
    connection = CrmConnection(
        account_id=account_id, provider=provider,
        external_ref="ext_ref_test", external_account_label=f"{provider.value.title()} (test)",
        access_token_encrypted=encrypt_secret("at_test"), refresh_token_encrypted=encrypt_secret("rt_test"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(connection)
    db_session.commit()
    db_session.refresh(connection)
    return connection


def test_connect_rejects_an_unknown_provider(client):
    token = _signup_and_login(client, "crm-owner2@example.com")
    response = client.post(
        "/crm/connect", json={"provider": "not-a-real-crm"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


def test_connect_rejects_hubspot_pointing_to_real_oauth_flow(client):
    """All three CrmProvider values are real OAuth integrations now (see
    the provider-specific test sections below) - the historical mock
    /crm/connect path must never hand out a fake connection
    indistinguishable from a real one."""
    token = _signup_and_login(client, "crm-owner-hs-reject@example.com")
    response = client.post(
        "/crm/connect", json={"provider": "hubspot"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "OAuth" in response.json()["detail"]


def test_connect_rejects_salesforce_pointing_to_real_oauth_flow(client):
    """Same rationale as the HubSpot rejection above."""
    token = _signup_and_login(client, "crm-owner-sf-reject@example.com")
    response = client.post(
        "/crm/connect", json={"provider": "salesforce"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "OAuth" in response.json()["detail"]


def test_connect_rejects_pipedrive_pointing_to_real_oauth_flow(client):
    """Same rationale as the HubSpot rejection above."""
    token = _signup_and_login(client, "crm-owner-pd-reject@example.com")
    response = client.post(
        "/crm/connect", json={"provider": "pipedrive"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "OAuth" in response.json()["detail"]


def test_disconnecting_a_crm_notifies_the_owner(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())
    token = _signup_and_login(client, "crm-owner1c@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()
    _insert_real_connection(db_session, me["account_id"])

    disconnect_response = client.post("/crm/disconnect", headers=headers)
    assert disconnect_response.status_code == 204

    notifications = client.get("/notifications/me", headers=headers).json()
    matches = [n for n in notifications if n["event_name"] == "intg.integration_removed"]
    assert len(matches) == 1
    assert matches[0]["status"] == "sent"


def test_member_cannot_get_a_hubspot_authorize_url(client):
    """RBAC now lives on the real authorize endpoints, not the dead mock
    /crm/connect path."""
    owner_token = _signup_and_login(client, "crm-owner4@example.com")
    client.post(
        "/team/members",
        json={"email": "crm-member4@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    member_token = client.post(
        "/auth/login", json={"email": "crm-member4@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.get("/crm/hubspot/authorize", headers={"Authorization": f"Bearer {member_token}"})
    assert response.status_code == 403


def test_disconnect_clears_the_slot_for_a_fresh_connection(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())
    token = _signup_and_login(client, "crm-owner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()
    _insert_real_connection(db_session, me["account_id"])

    disconnect_response = client.post("/crm/disconnect", headers=headers)
    assert disconnect_response.status_code == 204
    assert client.get("/crm/connection", headers=headers).json() is None


def test_disconnect_without_a_connection_returns_404(client):
    token = _signup_and_login(client, "crm-owner6@example.com")
    response = client.post("/crm/disconnect", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 404


def test_get_connection_is_null_when_none_exists(client):
    token = _signup_and_login(client, "crm-owner7@example.com")
    response = client.get("/crm/connection", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() is None


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
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.upsert_contact",
        lambda access_token, *, phone_number, name: {"external_ref": "hs_contact_notif_1"},
    )
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.log_activity",
        lambda access_token, *, contact_external_ref, event_type, note_body: {"external_ref": "hs_note_notif_1"},
    )

    token = _signup_and_login(client, "crm-activity1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()
    _insert_real_connection(db_session, me["account_id"])

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
    activity_events = [e for e in sync_log if e["event_type"] == "activity_sync" and e["payload"]["event_type"] == "number.activated"]
    assert len(activity_events) == 1
    assert activity_events[0]["external_ref"] == "hs_note_notif_1"


# --- Real HubSpot OAuth integration ---


def _configure_hubspot(monkeypatch):
    monkeypatch.setattr("app.integrations.crm.hubspot.settings.hubspot_client_id", "hs_client_test")
    monkeypatch.setattr("app.integrations.crm.hubspot.settings.hubspot_client_secret", "hs_secret_test")
    monkeypatch.setattr("app.integrations.crm.hubspot.settings.hubspot_redirect_uri", "http://localhost:8000/crm/hubspot/callback")
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())


def _test_fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def test_hubspot_authorize_requires_configured_credentials(client):
    token = _signup_and_login(client, "crm-hs-unconfigured@example.com")
    response = client.get("/crm/hubspot/authorize", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503


def test_hubspot_authorize_returns_a_real_authorize_url(client, monkeypatch):
    _configure_hubspot(monkeypatch)
    token = _signup_and_login(client, "crm-hs-authorize@example.com")
    response = client.get("/crm/hubspot/authorize", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert url.startswith("https://app.hubspot.com/oauth/authorize")
    assert "state=" in url
    assert "client_id=hs_client_test" in url


def test_hubspot_callback_completes_oauth_and_creates_a_real_connection(client, db_session, monkeypatch):
    _configure_hubspot(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.exchange_code_for_tokens",
        lambda code: {"access_token": "at_real_1", "refresh_token": "rt_real_1", "expires_in": 1800},
    )
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.get_hub_info",
        lambda access_token: {"hub_id": 12345, "hub_domain": "test-portal.hubspot.com", "label": "HubSpot (test-portal.hubspot.com)"},
    )

    token = _signup_and_login(client, "crm-hs-callback-ok@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    state = client.get("/crm/hubspot/authorize", headers=headers).json()["authorize_url"].split("state=")[1].split("&")[0]

    response = client.get(f"/crm/hubspot/callback?code=abc123&state={state}", follow_redirects=False)
    assert response.status_code in (302, 307)
    # /dashboard/business is the real page this UI lives on
    # (frontend/src/app/dashboard/business/page.tsx) - a prior version of
    # this redirect pointed at a nonexistent /dashboard/integrations.
    assert "/dashboard/business?crm=connected" in response.headers["location"]

    connection = client.get("/crm/connection", headers=headers).json()
    assert connection["provider"] == "hubspot"
    assert connection["external_account_label"] == "HubSpot (test-portal.hubspot.com)"


def test_hubspot_callback_rejects_invalid_state(client, monkeypatch):
    _configure_hubspot(monkeypatch)
    response = client.get("/crm/hubspot/callback?code=abc123&state=not-a-real-token", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "crm=error" in response.headers["location"]


def test_hubspot_callback_rejects_when_already_connected(client, monkeypatch):
    _configure_hubspot(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.exchange_code_for_tokens",
        lambda code: {"access_token": "at_real_2", "refresh_token": "rt_real_2", "expires_in": 1800},
    )
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.get_hub_info",
        lambda access_token: {"hub_id": 999, "hub_domain": "already.hubspot.com", "label": "HubSpot (already)"},
    )

    token = _signup_and_login(client, "crm-hs-callback-dup@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    state = client.get("/crm/hubspot/authorize", headers=headers).json()["authorize_url"].split("state=")[1].split("&")[0]

    first = client.get(f"/crm/hubspot/callback?code=abc123&state={state}", follow_redirects=False)
    assert "crm=connected" in first.headers["location"]

    second = client.get(f"/crm/hubspot/callback?code=abc123&state={state}", follow_redirects=False)
    assert "crm=error" in second.headers["location"]


def test_real_hubspot_connection_syncs_contacts_via_the_real_adapter(client, db_session, monkeypatch):
    _configure_hubspot(monkeypatch)
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.upsert_contact",
        lambda access_token, *, phone_number, name: {"external_ref": "hs_contact_real_1"},
    )

    token = _signup_and_login(client, "crm-hs-real-sync@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    from datetime import datetime, timedelta, timezone

    from app.core.crypto import encrypt_secret
    from app.crm.models import CrmConnection, CrmProvider

    connection = CrmConnection(
        account_id=me["account_id"], provider=CrmProvider.HUBSPOT,
        external_ref="12345", external_account_label="HubSpot (real)",
        access_token_encrypted=encrypt_secret("at_valid"), refresh_token_encrypted=encrypt_secret("rt_valid"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(connection)
    db_session.commit()

    client.post("/contacts", json={"name": "Real Sync Co", "phone_number": "+15557778888"}, headers=headers)

    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert len(sync_log) == 1
    assert sync_log[0]["external_ref"] == "hs_contact_real_1"


def test_real_hubspot_connection_refreshes_an_expired_token_before_syncing(client, db_session, monkeypatch):
    _configure_hubspot(monkeypatch)
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.upsert_contact",
        lambda access_token, *, phone_number, name: {"external_ref": f"hs_contact_{access_token}"},
    )
    refresh_calls = []

    def _fake_refresh(refresh_token):
        refresh_calls.append(refresh_token)
        return {"access_token": "at_refreshed", "refresh_token": "rt_refreshed", "expires_in": 1800}

    monkeypatch.setattr("app.crm.service.hubspot_adapter.refresh_access_token", _fake_refresh)

    token = _signup_and_login(client, "crm-hs-refresh@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    from datetime import datetime, timedelta, timezone

    from app.core.crypto import encrypt_secret
    from app.crm.models import CrmConnection, CrmProvider

    connection = CrmConnection(
        account_id=me["account_id"], provider=CrmProvider.HUBSPOT,
        external_ref="12345", external_account_label="HubSpot (real)",
        access_token_encrypted=encrypt_secret("at_expired"), refresh_token_encrypted=encrypt_secret("rt_expired"),
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),  # already expired
    )
    db_session.add(connection)
    db_session.commit()

    client.post("/contacts", json={"name": "Refresh Co", "phone_number": "+15556667777"}, headers=headers)

    assert refresh_calls == ["rt_expired"]
    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert sync_log[0]["external_ref"] == "hs_contact_at_refreshed"


# --- Real Salesforce OAuth integration ---


def _configure_salesforce(monkeypatch):
    monkeypatch.setattr("app.integrations.crm.salesforce.settings.salesforce_client_id", "sf_client_test")
    monkeypatch.setattr("app.integrations.crm.salesforce.settings.salesforce_client_secret", "sf_secret_test")
    monkeypatch.setattr("app.integrations.crm.salesforce.settings.salesforce_redirect_uri", "http://localhost:8000/crm/salesforce/callback")
    monkeypatch.setattr("app.integrations.crm.salesforce.settings.salesforce_login_base_url", "https://login.salesforce.com")
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())


def test_salesforce_authorize_requires_configured_credentials(client):
    token = _signup_and_login(client, "crm-sf-unconfigured@example.com")
    response = client.get("/crm/salesforce/authorize", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503


def test_salesforce_authorize_returns_a_real_authorize_url(client, monkeypatch):
    _configure_salesforce(monkeypatch)
    token = _signup_and_login(client, "crm-sf-authorize@example.com")
    response = client.get("/crm/salesforce/authorize", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert url.startswith("https://login.salesforce.com/services/oauth2/authorize")
    assert "state=" in url
    assert "client_id=sf_client_test" in url


def test_salesforce_callback_completes_oauth_and_creates_a_real_connection(client, db_session, monkeypatch):
    _configure_salesforce(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr(
        "app.crm.service.salesforce_adapter.exchange_code_for_tokens",
        lambda code: {
            "access_token": "at_real_1", "refresh_token": "rt_real_1",
            "instance_url": "https://test-org.my.salesforce.com", "identity_url": "https://login.salesforce.com/id/orgid/userid",
        },
    )
    monkeypatch.setattr(
        "app.crm.service.salesforce_adapter.get_org_label",
        lambda access_token, identity_url: "Salesforce (test@example.com)",
    )

    token = _signup_and_login(client, "crm-sf-callback-ok@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    state = client.get("/crm/salesforce/authorize", headers=headers).json()["authorize_url"].split("state=")[1].split("&")[0]

    response = client.get(f"/crm/salesforce/callback?code=abc123&state={state}", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/dashboard/business?crm=connected" in response.headers["location"]

    connection = client.get("/crm/connection", headers=headers).json()
    assert connection["provider"] == "salesforce"
    assert connection["external_account_label"] == "Salesforce (test@example.com)"


def test_salesforce_callback_rejects_invalid_state(client, monkeypatch):
    _configure_salesforce(monkeypatch)
    response = client.get("/crm/salesforce/callback?code=abc123&state=not-a-real-token", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "crm=error" in response.headers["location"]


def test_salesforce_callback_rejects_when_already_connected(client, monkeypatch):
    _configure_salesforce(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr(
        "app.crm.service.salesforce_adapter.exchange_code_for_tokens",
        lambda code: {
            "access_token": "at_real_2", "refresh_token": "rt_real_2",
            "instance_url": "https://test-org.my.salesforce.com", "identity_url": "https://login.salesforce.com/id/orgid/userid2",
        },
    )
    monkeypatch.setattr(
        "app.crm.service.salesforce_adapter.get_org_label",
        lambda access_token, identity_url: "Salesforce (already)",
    )

    token = _signup_and_login(client, "crm-sf-callback-dup@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    state = client.get("/crm/salesforce/authorize", headers=headers).json()["authorize_url"].split("state=")[1].split("&")[0]

    first = client.get(f"/crm/salesforce/callback?code=abc123&state={state}", follow_redirects=False)
    assert "crm=connected" in first.headers["location"]

    second = client.get(f"/crm/salesforce/callback?code=abc123&state={state}", follow_redirects=False)
    assert "crm=error" in second.headers["location"]


def test_real_salesforce_connection_syncs_contacts_via_the_real_adapter(client, db_session, monkeypatch):
    _configure_salesforce(monkeypatch)
    monkeypatch.setattr(
        "app.crm.service.salesforce_adapter.upsert_contact",
        lambda access_token, instance_url, *, phone_number, name: {"external_ref": "sf_contact_real_1"},
    )

    token = _signup_and_login(client, "crm-sf-real-sync@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    from app.core.crypto import encrypt_secret
    from app.crm.models import CrmConnection, CrmProvider

    connection = CrmConnection(
        account_id=me["account_id"], provider=CrmProvider.SALESFORCE,
        external_ref="https://login.salesforce.com/id/orgid/userid", external_account_label="Salesforce (real)",
        access_token_encrypted=encrypt_secret("at_valid"), refresh_token_encrypted=encrypt_secret("rt_valid"),
        instance_url="https://test-org.my.salesforce.com",
    )
    db_session.add(connection)
    db_session.commit()

    client.post("/contacts", json={"name": "Real Sync Co", "phone_number": "+15557778888"}, headers=headers)

    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert len(sync_log) == 1
    assert sync_log[0]["external_ref"] == "sf_contact_real_1"


def test_real_salesforce_connection_reauthenticates_on_a_401_before_syncing(client, db_session, monkeypatch):
    """Unlike HubSpot's pre-emptive refresh-before-expiry, Salesforce has no
    told-to-you expiry - this exercises the reactive path: the first call
    with the stored (stale) token 401s, triggering exactly one refresh and
    retry with the new token."""
    _configure_salesforce(monkeypatch)

    from app.integrations.crm import salesforce as salesforce_adapter_module

    call_tokens = []

    def _fake_upsert(access_token, instance_url, *, phone_number, name):
        call_tokens.append(access_token)
        if access_token == "at_stale":
            raise salesforce_adapter_module.SalesforceAuthExpiredError("401")
        return {"external_ref": f"sf_contact_{access_token}"}

    monkeypatch.setattr("app.crm.service.salesforce_adapter.upsert_contact", _fake_upsert)

    refresh_calls = []

    def _fake_refresh(refresh_token):
        refresh_calls.append(refresh_token)
        return {"access_token": "at_fresh", "instance_url": "https://test-org.my.salesforce.com"}

    monkeypatch.setattr("app.crm.service.salesforce_adapter.refresh_access_token", _fake_refresh)

    token = _signup_and_login(client, "crm-sf-reauth@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    from app.core.crypto import encrypt_secret
    from app.crm.models import CrmConnection, CrmProvider

    connection = CrmConnection(
        account_id=me["account_id"], provider=CrmProvider.SALESFORCE,
        external_ref="https://login.salesforce.com/id/orgid/userid", external_account_label="Salesforce (real)",
        access_token_encrypted=encrypt_secret("at_stale"), refresh_token_encrypted=encrypt_secret("rt_valid"),
        instance_url="https://test-org.my.salesforce.com",
    )
    db_session.add(connection)
    db_session.commit()

    client.post("/contacts", json={"name": "Reauth Co", "phone_number": "+15556667777"}, headers=headers)

    assert call_tokens == ["at_stale", "at_fresh"]
    assert refresh_calls == ["rt_valid"]
    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert sync_log[0]["external_ref"] == "sf_contact_at_fresh"


# --- Real Pipedrive OAuth integration ---


def _configure_pipedrive(monkeypatch):
    monkeypatch.setattr("app.integrations.crm.pipedrive.settings.pipedrive_client_id", "pd_client_test")
    monkeypatch.setattr("app.integrations.crm.pipedrive.settings.pipedrive_client_secret", "pd_secret_test")
    monkeypatch.setattr("app.integrations.crm.pipedrive.settings.pipedrive_redirect_uri", "http://localhost:8000/crm/pipedrive/callback")
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())


def test_pipedrive_authorize_requires_configured_credentials(client):
    token = _signup_and_login(client, "crm-pd-unconfigured@example.com")
    response = client.get("/crm/pipedrive/authorize", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503


def test_pipedrive_authorize_returns_a_real_authorize_url(client, monkeypatch):
    _configure_pipedrive(monkeypatch)
    token = _signup_and_login(client, "crm-pd-authorize@example.com")
    response = client.get("/crm/pipedrive/authorize", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    url = response.json()["authorize_url"]
    assert url.startswith("https://oauth.pipedrive.com/oauth/authorize")
    assert "state=" in url
    assert "client_id=pd_client_test" in url


def test_pipedrive_callback_completes_oauth_and_creates_a_real_connection(client, db_session, monkeypatch):
    _configure_pipedrive(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr(
        "app.crm.service.pipedrive_adapter.exchange_code_for_tokens",
        lambda code: {
            "access_token": "at_real_1", "refresh_token": "rt_real_1",
            "api_domain": "https://test-co.pipedrive.com", "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        "app.crm.service.pipedrive_adapter.get_account_label",
        lambda access_token, api_domain: "Pipedrive (Test Co)",
    )

    token = _signup_and_login(client, "crm-pd-callback-ok@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    state = client.get("/crm/pipedrive/authorize", headers=headers).json()["authorize_url"].split("state=")[1].split("&")[0]

    response = client.get(f"/crm/pipedrive/callback?code=abc123&state={state}", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "/dashboard/business?crm=connected" in response.headers["location"]

    connection = client.get("/crm/connection", headers=headers).json()
    assert connection["provider"] == "pipedrive"
    assert connection["external_account_label"] == "Pipedrive (Test Co)"


def test_pipedrive_callback_rejects_invalid_state(client, monkeypatch):
    _configure_pipedrive(monkeypatch)
    response = client.get("/crm/pipedrive/callback?code=abc123&state=not-a-real-token", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "crm=error" in response.headers["location"]


def test_pipedrive_callback_rejects_when_already_connected(client, monkeypatch):
    _configure_pipedrive(monkeypatch)
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr(
        "app.crm.service.pipedrive_adapter.exchange_code_for_tokens",
        lambda code: {
            "access_token": "at_real_2", "refresh_token": "rt_real_2",
            "api_domain": "https://test-co.pipedrive.com", "expires_in": 3600,
        },
    )
    monkeypatch.setattr(
        "app.crm.service.pipedrive_adapter.get_account_label",
        lambda access_token, api_domain: "Pipedrive (already)",
    )

    token = _signup_and_login(client, "crm-pd-callback-dup@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    state = client.get("/crm/pipedrive/authorize", headers=headers).json()["authorize_url"].split("state=")[1].split("&")[0]

    first = client.get(f"/crm/pipedrive/callback?code=abc123&state={state}", follow_redirects=False)
    assert "crm=connected" in first.headers["location"]

    second = client.get(f"/crm/pipedrive/callback?code=abc123&state={state}", follow_redirects=False)
    assert "crm=error" in second.headers["location"]


def test_real_pipedrive_connection_syncs_contacts_via_the_real_adapter(client, db_session, monkeypatch):
    _configure_pipedrive(monkeypatch)
    monkeypatch.setattr(
        "app.crm.service.pipedrive_adapter.upsert_contact",
        lambda access_token, api_domain, *, phone_number, name: {"external_ref": "pd_contact_real_1"},
    )

    token = _signup_and_login(client, "crm-pd-real-sync@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    connection = CrmConnection(
        account_id=me["account_id"], provider=CrmProvider.PIPEDRIVE,
        external_ref="https://test-co.pipedrive.com", external_account_label="Pipedrive (real)",
        access_token_encrypted=encrypt_secret("at_valid"), refresh_token_encrypted=encrypt_secret("rt_valid"),
        instance_url="https://test-co.pipedrive.com",
        token_expires_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )
    db_session.add(connection)
    db_session.commit()

    client.post("/contacts", json={"name": "Real Sync Co", "phone_number": "+15557778888"}, headers=headers)

    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert len(sync_log) == 1
    assert sync_log[0]["external_ref"] == "pd_contact_real_1"


def test_real_pipedrive_connection_refreshes_an_expired_token_before_syncing(client, db_session, monkeypatch):
    _configure_pipedrive(monkeypatch)
    monkeypatch.setattr(
        "app.crm.service.pipedrive_adapter.upsert_contact",
        lambda access_token, api_domain, *, phone_number, name: {"external_ref": f"pd_contact_{access_token}"},
    )
    refresh_calls = []

    def _fake_refresh(refresh_token):
        refresh_calls.append(refresh_token)
        return {
            "access_token": "at_refreshed", "refresh_token": "rt_refreshed",
            "api_domain": "https://test-co.pipedrive.com", "expires_in": 3600,
        }

    monkeypatch.setattr("app.crm.service.pipedrive_adapter.refresh_access_token", _fake_refresh)

    token = _signup_and_login(client, "crm-pd-refresh@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    connection = CrmConnection(
        account_id=me["account_id"], provider=CrmProvider.PIPEDRIVE,
        external_ref="https://test-co.pipedrive.com", external_account_label="Pipedrive (real)",
        access_token_encrypted=encrypt_secret("at_expired"), refresh_token_encrypted=encrypt_secret("rt_expired"),
        instance_url="https://test-co.pipedrive.com",
        token_expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),  # already expired
    )
    db_session.add(connection)
    db_session.commit()

    client.post("/contacts", json={"name": "Refresh Co", "phone_number": "+15556667777"}, headers=headers)

    assert refresh_calls == ["rt_expired"]
    sync_log = client.get("/crm/sync-log", headers=headers).json()
    assert sync_log[0]["external_ref"] == "pd_contact_at_refreshed"


def test_sync_log_is_scoped_to_the_callers_account(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.crypto.settings.token_encryption_key", _test_fernet_key())
    monkeypatch.setattr(
        "app.crm.service.hubspot_adapter.upsert_contact",
        lambda access_token, *, phone_number, name: {"external_ref": "hs_contact_scope_1"},
    )
    token_a = _signup_and_login(client, "crm-scope-a@example.com", "Scope A Co")
    token_b = _signup_and_login(client, "crm-scope-b@example.com", "Scope B Co")
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}
    me_a = client.get("/auth/me", headers=headers_a).json()

    _insert_real_connection(db_session, me_a["account_id"])
    client.post("/contacts", json={"name": "A Contact", "phone_number": "+15551110000"}, headers=headers_a)

    assert len(client.get("/crm/sync-log", headers=headers_a).json()) >= 1
    assert client.get("/crm/sync-log", headers=headers_b).json() == []
