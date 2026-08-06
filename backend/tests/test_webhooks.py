def _signup_and_login(client, email: str, account_name: str = "Webhook Test Co") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": account_name, "account_type": "business", "email": email, "password": "supersecret123"},
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_owner_can_create_a_webhook_endpoint(client):
    token = _signup_and_login(client, "wh-owner1@example.com")
    response = client.post(
        "/webhooks/endpoints",
        json={"url": "https://example.com/hook", "description": "My CRM"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["url"] == "https://example.com/hook"
    assert body["is_active"] is True
    # Secret is real and only present on the create response.
    assert len(body["secret"]) == 64


def test_creating_an_endpoint_rejects_non_https_urls(client):
    token = _signup_and_login(client, "wh-owner2@example.com")
    response = client.post(
        "/webhooks/endpoints", json={"url": "http://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_member_cannot_create_a_webhook_endpoint(client):
    owner_token = _signup_and_login(client, "wh-owner3@example.com")
    client.post(
        "/team/members",
        json={"email": "wh-member3@example.com", "password": "supersecret123", "role": "member"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    member_token = client.post(
        "/auth/login", json={"email": "wh-member3@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_list_endpoints_does_not_include_the_secret(client):
    token = _signup_and_login(client, "wh-owner4@example.com")
    client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = client.get("/webhooks/endpoints", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "secret" not in body[0]


def test_owner_can_delete_their_own_endpoint(client):
    token = _signup_and_login(client, "wh-owner5@example.com")
    created = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    response = client.delete(f"/webhooks/endpoints/{created['id']}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204

    listed = client.get("/webhooks/endpoints", headers={"Authorization": f"Bearer {token}"}).json()
    assert listed == []


def test_cannot_delete_another_accounts_endpoint(client):
    owner_a_token = _signup_and_login(client, "wh-owner6a@example.com")
    owner_b_token = _signup_and_login(client, "wh-owner6b@example.com")

    created = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {owner_a_token}"},
    ).json()

    response = client.delete(
        f"/webhooks/endpoints/{created['id']}", headers={"Authorization": f"Bearer {owner_b_token}"}
    )
    assert response.status_code == 403


def test_endpoint_limit_is_enforced(client):
    token = _signup_and_login(client, "wh-owner7@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    for i in range(10):
        response = client.post("/webhooks/endpoints", json={"url": f"https://example.com/hook{i}"}, headers=headers)
        assert response.status_code == 201

    response = client.post("/webhooks/endpoints", json={"url": "https://example.com/hook-overflow"}, headers=headers)
    assert response.status_code == 409


def test_dispatch_sends_a_signed_post_to_a_registered_endpoint(client, db_session, monkeypatch):
    import hashlib
    import hmac
    import json

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token = _signup_and_login(client, "wh-dispatch1@example.com")
    created = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    secret = created["secret"]

    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True

    def fake_post(url, *, content, headers, timeout):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setattr("app.webhooks.service.httpx.post", fake_post)

    from app.notifications.service import send_notification

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    send_notification(
        db_session, event_name="number.activated", account_id=me["account_id"],
        recipient_email="wh-dispatch1@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Webhook Test Co", "user_display_name": "wh-dispatch1@example.com",
        },
    )

    assert captured["url"] == "https://example.com/hook"
    body = json.loads(captured["content"])
    assert body["event_type"] == "number.activated"
    assert body["data"]["e164"] == "+15550001111"

    expected_signature = hmac.new(secret.encode(), captured["content"], hashlib.sha256).hexdigest()
    assert captured["headers"]["X-Zoiko-Signature"] == f"sha256={expected_signature}"
    assert captured["headers"]["X-Zoiko-Event"] == "number.activated"

    deliveries = client.get("/webhooks/deliveries", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "delivered"
    assert deliveries[0]["response_status_code"] == 200


def test_dispatch_records_a_failed_delivery_without_raising(client, db_session, monkeypatch):
    import httpx

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token = _signup_and_login(client, "wh-dispatch2@example.com")
    client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )

    def fake_post(url, *, content, headers, timeout):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr("app.webhooks.service.httpx.post", fake_post)

    from app.notifications.service import send_notification

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    # Must not raise - a webhook delivery failure is best-effort, same as SMS.
    send_notification(
        db_session, event_name="number.activated", account_id=me["account_id"],
        recipient_email="wh-dispatch2@example.com",
        context={
            "e164": "+15550002222", "number_formatted": "+15550002222",
            "organization_name": "Webhook Test Co", "user_display_name": "wh-dispatch2@example.com",
        },
    )

    deliveries = client.get("/webhooks/deliveries", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "failed"
    assert "simulated network failure" in deliveries[0]["error"]


def test_inactive_endpoint_receives_no_deliveries(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token = _signup_and_login(client, "wh-inactive@example.com")
    client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )

    from app.numbering.identity.models import Account
    from app.webhooks.models import WebhookEndpoint

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    endpoint = db_session.query(WebhookEndpoint).filter(WebhookEndpoint.account_id == me["account_id"]).first()
    endpoint.is_active = False
    db_session.commit()

    called = []
    monkeypatch.setattr("app.webhooks.service.httpx.post", lambda *a, **kw: called.append(1))

    from app.notifications.service import send_notification

    send_notification(
        db_session, event_name="number.activated", account_id=me["account_id"],
        recipient_email="wh-inactive@example.com",
        context={
            "e164": "+15550003333", "number_formatted": "+15550003333",
            "organization_name": "Webhook Test Co", "user_display_name": "wh-inactive@example.com",
        },
    )
    assert called == []
