def _signup_and_login(client, email: str, account_name: str = "Public API Test Co") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": account_name, "account_type": "business", "email": email, "password": "supersecret123"},
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_owner_can_create_an_api_key(client):
    token = _signup_and_login(client, "api-owner1@example.com")
    response = client.post(
        "/developer/api-keys", json={"label": "My Server"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["label"] == "My Server"
    assert body["raw_key"].startswith("zlk_live_")
    assert body["key_prefix"] == body["raw_key"][:16]


def test_creating_an_api_key_notifies_the_owner(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token = _signup_and_login(client, "api-owner1b@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/developer/api-keys", json={"label": "Notify Me"}, headers=headers)

    notifications = client.get("/notifications/me", headers=headers).json()
    matches = [n for n in notifications if n["event_name"] == "intg.api_client_created"]
    assert len(matches) == 1
    assert matches[0]["status"] == "sent"


def test_member_cannot_create_an_api_key(client):
    owner_token = _signup_and_login(client, "api-owner2@example.com")
    client.post(
        "/team/members",
        json={"email": "api-member2@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    member_token = client.post(
        "/auth/login", json={"email": "api-member2@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/developer/api-keys", json={"label": "Nope"}, headers={"Authorization": f"Bearer {member_token}"}
    )
    assert response.status_code == 403


def test_list_api_keys_does_not_expose_raw_key(client):
    token = _signup_and_login(client, "api-owner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/developer/api-keys", json={"label": "Key A"}, headers=headers)

    response = client.get("/developer/api-keys", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "raw_key" not in body[0]
    assert "key_hash" not in body[0]


def test_owner_can_revoke_a_key_and_it_stops_authenticating(client):
    token = _signup_and_login(client, "api-owner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/developer/api-keys", json={"label": "Revoke me"}, headers=headers).json()

    response = client.get(
        "/public/v1/numbers", headers={"Authorization": f"Bearer {created['raw_key']}"}
    )
    assert response.status_code == 200

    delete_response = client.delete(f"/developer/api-keys/{created['id']}", headers=headers)
    assert delete_response.status_code == 204

    response = client.get(
        "/public/v1/numbers", headers={"Authorization": f"Bearer {created['raw_key']}"}
    )
    assert response.status_code == 401


def test_key_limit_is_enforced(client):
    token = _signup_and_login(client, "api-owner5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    for i in range(10):
        response = client.post("/developer/api-keys", json={"label": f"Key {i}"}, headers=headers)
        assert response.status_code == 201

    response = client.post("/developer/api-keys", json={"label": "Overflow"}, headers=headers)
    assert response.status_code == 409


def test_public_api_rejects_missing_or_invalid_key(client):
    response = client.get("/public/v1/numbers")
    assert response.status_code == 401

    response = client.get("/public/v1/numbers", headers={"Authorization": "Bearer zlk_live_totallyfake"})
    assert response.status_code == 401


def test_public_api_scopes_data_to_the_keys_own_account(client):
    token_a = _signup_and_login(client, "api-scope-a@example.com", "Scope A Co")
    token_b = _signup_and_login(client, "api-scope-b@example.com", "Scope B Co")

    key_a = client.post(
        "/developer/api-keys", json={"label": "A"}, headers={"Authorization": f"Bearer {token_a}"}
    ).json()["raw_key"]
    key_b = client.post(
        "/developer/api-keys", json={"label": "B"}, headers={"Authorization": f"Bearer {token_b}"}
    ).json()["raw_key"]

    resp_a = client.get("/public/v1/numbers", headers={"Authorization": f"Bearer {key_a}"})
    resp_b = client.get("/public/v1/numbers", headers={"Authorization": f"Bearer {key_b}"})
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    # Both accounts have zero numbers, but distinctly - this mainly proves
    # the auth dependency resolves to the right account without crossing.
    assert resp_a.json() == []
    assert resp_b.json() == []


def test_public_api_calls_voicemails_summaries_endpoints_smoke(client):
    token = _signup_and_login(client, "api-smoke@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "Smoke"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}

    assert client.get("/public/v1/calls", headers=headers).status_code == 200
    assert client.get("/public/v1/voicemails", headers=headers).status_code == 200
    assert client.get("/public/v1/summaries", headers=headers).status_code == 200


# --- Write/action endpoints ---


def _make_active_number(client, db_session, token, e164: str):
    from datetime import datetime, timezone

    from app.numbering.numbers.models import CallerIdentity, CallerIdentityStatus, PhoneNumber, PhoneNumberStatus

    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    # Real purchases auto-create a VERIFIED CallerIdentity (see
    # assert_caller_id_authorized) - this helper bypasses purchase_number
    # entirely, so it must create one itself or outbound calls get rejected
    # as an unauthorized caller ID.
    db_session.add(CallerIdentity(
        phone_number_id=number.id, account_id=account_id, status=CallerIdentityStatus.VERIFIED,
        verification_source="test-fixture", verified_at=datetime.now(timezone.utc),
    ))
    db_session.commit()


def test_public_api_can_place_an_outbound_call(client, db_session, monkeypatch):
    token = _signup_and_login(client, "api-call1@example.com")
    _make_active_number(client, db_session, token, "+15550001111")
    key = client.post(
        "/developer/api-keys", json={"label": "Caller"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]

    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kw: {"sid": "CApublicapi1", "status": "queued", "to": kw["to"], "from": kw["from_"]},
    )

    response = client.post(
        "/public/v1/calls",
        json={"to": "+15559998888", "from_number": "+15550001111", "message": "hello"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["sid"] == "CApublicapi1"
    assert body["status"] == "queued"

    calls = client.get("/public/v1/calls", headers={"Authorization": f"Bearer {key}"}).json()
    assert len(calls) == 1
    assert calls[0]["to_number"] == "+15559998888"


def test_public_api_call_rejects_a_number_not_owned_by_the_key_account(client, db_session):
    token = _signup_and_login(client, "api-call2@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "Caller"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]

    response = client.post(
        "/public/v1/calls",
        json={"to": "+15559998888", "from_number": "+15550009999"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 403


def test_public_api_can_create_and_list_contacts(client):
    token = _signup_and_login(client, "api-contact1@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "CRM sync"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}

    create_response = client.post(
        "/public/v1/contacts",
        json={"name": "Jane Doe", "phone_number": "+15551234567", "email": "jane@example.com"},
        headers=headers,
    )
    assert create_response.status_code == 201
    assert create_response.json()["name"] == "Jane Doe"

    listed = client.get("/public/v1/contacts", headers=headers).json()
    assert len(listed) == 1
    assert listed[0]["phone_number"] == "+15551234567"


def test_public_api_contacts_are_scoped_to_the_keys_own_account(client):
    token_a = _signup_and_login(client, "api-contact-a@example.com", "Contact Scope A")
    token_b = _signup_and_login(client, "api-contact-b@example.com", "Contact Scope B")
    key_a = client.post(
        "/developer/api-keys", json={"label": "A"}, headers={"Authorization": f"Bearer {token_a}"}
    ).json()["raw_key"]
    key_b = client.post(
        "/developer/api-keys", json={"label": "B"}, headers={"Authorization": f"Bearer {token_b}"}
    ).json()["raw_key"]

    client.post(
        "/public/v1/contacts", json={"name": "A Contact", "phone_number": "+15551110000"},
        headers={"Authorization": f"Bearer {key_a}"},
    )

    assert len(client.get("/public/v1/contacts", headers={"Authorization": f"Bearer {key_a}"}).json()) == 1
    assert client.get("/public/v1/contacts", headers={"Authorization": f"Bearer {key_b}"}).json() == []


# --- Usage summary ---


def test_public_api_returns_usage_summary(client):
    token = _signup_and_login(client, "api-usage1@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "Usage"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]

    response = client.get("/public/v1/usage", headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == 200
    body = response.json()
    assert "plan_code" in body
    assert "resources" in body


# --- Webhook management ---


def _mock_webhook_delivery(monkeypatch):
    """Avoids a real network call (and its multi-second timeout) to
    https://example.com on every webhook-creating test - same pattern
    test_webhooks.py's test_creating_an_endpoint_notifies_the_owner uses.
    Also blanks resend_api_key: httpx.post is a single shared module-level
    function, so patching app.webhooks.service.httpx.post patches it for
    every other httpx.post call in the process too (e.g. Resend's real
    send_email) - without this, signup's real welcome-email attempt would
    hit the fake response and crash on a missing raise_for_status()."""

    class FakeResponse:
        status_code = 200
        is_success = True

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.webhooks.service.httpx.post", lambda *a, **kw: FakeResponse())


def test_public_api_can_create_list_and_delete_a_webhook(client, monkeypatch):
    _mock_webhook_delivery(monkeypatch)
    token = _signup_and_login(client, "api-webhook1@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "Webhooks"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}

    create_response = client.post(
        "/public/v1/webhooks", json={"url": "https://example.com/hook", "description": "test hook"}, headers=headers
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["url"] == "https://example.com/hook"
    assert "secret" in body

    listed = client.get("/public/v1/webhooks", headers=headers).json()
    assert len(listed) == 1
    assert "secret" not in listed[0]

    delete_response = client.delete(f"/public/v1/webhooks/{body['id']}", headers=headers)
    assert delete_response.status_code == 204
    assert client.get("/public/v1/webhooks", headers=headers).json() == []


def test_public_api_rejects_a_non_https_webhook_url(client):
    token = _signup_and_login(client, "api-webhook2@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "Webhooks"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]

    response = client.post(
        "/public/v1/webhooks", json={"url": "http://not-https.example.com"},
        headers={"Authorization": f"Bearer {key}"},
    )
    assert response.status_code == 422


def test_public_api_cannot_delete_another_accounts_webhook(client, monkeypatch):
    _mock_webhook_delivery(monkeypatch)
    token_a = _signup_and_login(client, "api-webhook-a@example.com", "Webhook Scope A")
    token_b = _signup_and_login(client, "api-webhook-b@example.com", "Webhook Scope B")
    key_a = client.post(
        "/developer/api-keys", json={"label": "A"}, headers={"Authorization": f"Bearer {token_a}"}
    ).json()["raw_key"]
    key_b = client.post(
        "/developer/api-keys", json={"label": "B"}, headers={"Authorization": f"Bearer {token_b}"}
    ).json()["raw_key"]

    endpoint = client.post(
        "/public/v1/webhooks", json={"url": "https://a.example.com/hook"},
        headers={"Authorization": f"Bearer {key_a}"},
    ).json()

    response = client.delete(f"/public/v1/webhooks/{endpoint['id']}", headers={"Authorization": f"Bearer {key_b}"})
    assert response.status_code == 404


def test_public_api_webhook_creation_notifies_the_owner(client, monkeypatch):
    _mock_webhook_delivery(monkeypatch)
    token = _signup_and_login(client, "api-webhook3@example.com")
    key = client.post(
        "/developer/api-keys", json={"label": "Webhooks"}, headers={"Authorization": f"Bearer {token}"}
    ).json()["raw_key"]

    client.post(
        "/public/v1/webhooks", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {key}"},
    )

    notifications = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"}).json()
    matches = [n for n in notifications if n["event_name"] == "intg.webhook_endpoint_added"]
    assert len(matches) == 1


def test_public_api_lists_webhook_deliveries(client, db_session, monkeypatch):
    _mock_webhook_delivery(monkeypatch)
    token = _signup_and_login(client, "api-webhook4@example.com")
    headers_session = {"Authorization": f"Bearer {token}"}
    key = client.post(
        "/developer/api-keys", json={"label": "Webhooks"}, headers=headers_session
    ).json()["raw_key"]
    headers = {"Authorization": f"Bearer {key}"}

    endpoint = client.post(
        "/public/v1/webhooks", json={"url": "https://example.com/hook"}, headers=headers
    ).json()

    from app.webhooks import service as webhooks_service

    me = client.get("/auth/me", headers=headers_session).json()
    webhooks_service.dispatch_webhook_event(
        db_session, account_id=me["account_id"], event_type="test.event", payload={"foo": "bar"}
    )

    deliveries = client.get("/public/v1/webhooks/deliveries", headers=headers).json()
    # Creating the endpoint itself already fired one delivery (the
    # "webhook endpoint added" notification dispatches to endpoints that
    # already exist by the time it sends, including this one) - filter for
    # the explicit test event rather than assuming it's the only delivery.
    test_deliveries = [d for d in deliveries if d["event_type"] == "test.event"]
    assert len(test_deliveries) == 1

    filtered = client.get(
        "/public/v1/webhooks/deliveries", params={"endpoint_id": endpoint["id"]}, headers=headers
    ).json()
    assert any(d["event_type"] == "test.event" for d in filtered)
