def _signup_and_login(client, email: str, account_name: str = "Webhook Test Co") -> str:
    client.post(
        "/auth/signup",
        json={"account_name": account_name, "account_type": "business", "email": email, "password": "supersecret123"},
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _signup_login_and_upgrade(client, db_session, email: str, account_name: str = "Webhook Test Co") -> str:
    """Real gap fix (ZL-COM-ENT-001): webhooks are gated on
    developer.webhooks.scope >= limited (Starter = none, Business+ =
    limited), same as the /public/v1 API-key surface already required -
    the dashboard route creating a webhook with no entitlement check at
    all was the actual gap. Every test that creates a webhook endpoint
    needs a plan that grants this scope first."""
    from app.billing import service as billing_service

    token = _signup_and_login(client, email, account_name)
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")
    return token


def test_owner_can_create_a_webhook_endpoint(client, db_session):
    token = _signup_login_and_upgrade(client, db_session, "wh-owner1@example.com")
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


def test_creating_an_endpoint_notifies_the_owner(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    class FakeResponse:
        status_code = 200
        is_success = True

    monkeypatch.setattr("app.webhooks.service.httpx.post", lambda *a, **kw: FakeResponse())

    token = _signup_login_and_upgrade(client, db_session, "wh-owner1b@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/webhooks/endpoints", json={"url": "https://example.com/hook"}, headers=headers)

    notifications = client.get("/notifications/me", headers=headers).json()
    matches = [n for n in notifications if n["event_name"] == "intg.webhook_endpoint_added"]
    assert len(matches) == 1
    assert matches[0]["status"] == "sent"


def test_creating_an_endpoint_rejects_non_https_urls(client, db_session):
    token = _signup_login_and_upgrade(client, db_session, "wh-owner2@example.com")
    response = client.post(
        "/webhooks/endpoints", json={"url": "http://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_creating_an_endpoint_rejects_private_and_loopback_targets(client, db_session):
    """Real gap fix: _assert_valid_url only checked for an https:// prefix,
    with no check against private/loopback/link-local/reserved IP ranges -
    any account admin could register a webhook endpoint and this backend
    would then make real outbound HTTP calls to it on every event
    (dispatch_webhook_event), a blind SSRF vector against internal
    infrastructure (e.g. cloud metadata services, other services on this
    host's own network). These are all real, syntactically-valid https://
    URLs (unlike test_creating_an_endpoint_rejects_non_https_urls above) -
    only the resolved-address check should be what rejects them."""
    token = _signup_login_and_upgrade(client, db_session, "wh-owner-ssrf1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    disallowed_urls = [
        "https://169.254.169.254/latest/meta-data/",  # cloud metadata service
        "https://127.0.0.1/hook",  # loopback
        "https://localhost/hook",  # loopback via hostname
        "https://10.0.0.5/hook",  # RFC1918 private range
        "https://192.168.1.1/hook",  # RFC1918 private range
        "https://0.0.0.0/hook",  # unspecified
    ]
    for url in disallowed_urls:
        response = client.post("/webhooks/endpoints", json={"url": url}, headers=headers)
        assert response.status_code == 422, f"{url} should have been rejected, got {response.status_code}"

    # A genuine public HTTPS URL must still be accepted - the fix must not
    # be so broad it breaks the legitimate case every other test relies on.
    allowed = client.post("/webhooks/endpoints", json={"url": "https://example.com/hook"}, headers=headers)
    assert allowed.status_code == 201, allowed.text


def test_member_cannot_create_a_webhook_endpoint(client, db_session):
    from app.billing import service as billing_service

    owner_token = _signup_and_login(client, "wh-owner3@example.com")
    # Real gap fix (ZL-COM-ENT-001): adding a team member now requires
    # team.members.enabled (Business+).
    owner_account_id = client.get(
        "/auth/me", headers={"Authorization": f"Bearer {owner_token}"}
    ).json()["account_id"]
    billing_service.change_plan(db_session, owner_account_id, "business", actor="test-setup")
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


def test_list_endpoints_does_not_include_the_secret(client, db_session):
    token = _signup_login_and_upgrade(client, db_session, "wh-owner4@example.com")
    client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = client.get("/webhooks/endpoints", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert "secret" not in body[0]


def test_owner_can_delete_their_own_endpoint(client, db_session):
    token = _signup_login_and_upgrade(client, db_session, "wh-owner5@example.com")
    created = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()

    response = client.delete(f"/webhooks/endpoints/{created['id']}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 204

    listed = client.get("/webhooks/endpoints", headers={"Authorization": f"Bearer {token}"}).json()
    assert listed == []


def test_cannot_delete_another_accounts_endpoint(client, db_session):
    owner_a_token = _signup_login_and_upgrade(client, db_session, "wh-owner6a@example.com")
    owner_b_token = _signup_and_login(client, "wh-owner6b@example.com")

    created = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {owner_a_token}"},
    ).json()

    response = client.delete(
        f"/webhooks/endpoints/{created['id']}", headers={"Authorization": f"Bearer {owner_b_token}"}
    )
    assert response.status_code == 403


def test_endpoint_limit_is_enforced(client, db_session):
    token = _signup_login_and_upgrade(client, db_session, "wh-owner7@example.com")
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

    captured = {}

    class FakeResponse:
        status_code = 200
        is_success = True

    def fake_post(url, *, content, headers, timeout):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return FakeResponse()

    # Mocked before the endpoint is created, not after - creating an
    # endpoint now self-dispatches an intg.webhook_endpoint_added event to
    # it (see app.webhooks.service.create_endpoint), so a real POST would
    # otherwise fire against this fake URL before this test ever gets to it.
    monkeypatch.setattr("app.webhooks.service.httpx.post", fake_post)

    token = _signup_login_and_upgrade(client, db_session, "wh-dispatch1@example.com")
    created = client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    secret = created["secret"]

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
    activated_deliveries = [d for d in deliveries if d["event_type"] == "number.activated"]
    assert len(activated_deliveries) == 1
    assert activated_deliveries[0]["status"] == "delivered"
    assert activated_deliveries[0]["response_status_code"] == 200


def test_dispatch_records_a_failed_delivery_without_raising(client, db_session, monkeypatch):
    import httpx

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    def fake_post(url, *, content, headers, timeout):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr("app.webhooks.service.httpx.post", fake_post)

    token = _signup_login_and_upgrade(client, db_session, "wh-dispatch2@example.com")
    client.post(
        "/webhooks/endpoints", json={"url": "https://example.com/hook"},
        headers={"Authorization": f"Bearer {token}"},
    )

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
    activated_deliveries = [d for d in deliveries if d["event_type"] == "number.activated"]
    assert len(activated_deliveries) == 1
    assert activated_deliveries[0]["status"] == "failed"
    assert "simulated network failure" in activated_deliveries[0]["error"]


def test_inactive_endpoint_receives_no_deliveries(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    class FakeResponse:
        status_code = 200
        is_success = True

    monkeypatch.setattr("app.webhooks.service.httpx.post", lambda *a, **kw: FakeResponse())

    token = _signup_login_and_upgrade(client, db_session, "wh-inactive@example.com")
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
