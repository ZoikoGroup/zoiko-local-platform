import base64
import hashlib
import hmac
import json

from app.notifications.models import NotificationDeliveryStatus, SuppressionReason
from app.notifications.service import (
    add_suppression,
    check_suppression,
    decode_unsubscribe_token,
    send_notification,
    unsubscribe_via_token,
    update_preference,
    _create_unsubscribe_token,
)
from app.numbering.identity.models import Account, AccountType


def _account(db_session, name: str) -> str:
    account = Account(name=name, account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    return account.id


def _stub_send_email(monkeypatch, message_id: str | None = "resend_msg_1"):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "fake-key-for-test")
    sent = []

    def _fake_send(**kwargs):
        sent.append(kwargs)
        return message_id

    monkeypatch.setattr("app.notifications.service.send_email", _fake_send)
    return sent


# --- check_suppression / add_suppression ---

def test_add_suppression_is_idempotent(db_session):
    first = add_suppression(
        db_session, recipient_email="bounced@example.com", domain=None, reason=SuppressionReason.HARD_BOUNCE
    )
    second = add_suppression(
        db_session, recipient_email="bounced@example.com", domain=None, reason=SuppressionReason.HARD_BOUNCE
    )
    assert first.id == second.id


def test_global_suppression_blocks_domain_scoped_lookup(db_session):
    add_suppression(db_session, recipient_email="bad@example.com", domain=None, reason=SuppressionReason.HARD_BOUNCE)
    result = check_suppression(db_session, "bad@example.com", "BILL", is_exempt=False)
    assert result is not None
    assert result.reason == SuppressionReason.HARD_BOUNCE


def test_global_suppression_blocks_even_exempt_templates(db_session):
    """Doc §11.1: 'an invalid address is never overridden' - unlike a
    domain-scoped unsubscribe, a hard bounce/complaint blocks everything."""
    add_suppression(db_session, recipient_email="bad2@example.com", domain=None, reason=SuppressionReason.COMPLAINT)
    result = check_suppression(db_session, "bad2@example.com", "AUTH", is_exempt=True)
    assert result is not None


def test_domain_scoped_suppression_does_not_block_other_domains(db_session):
    add_suppression(
        db_session, recipient_email="picky@example.com", domain="BILL", reason=SuppressionReason.MANUAL_UNSUBSCRIBE
    )
    assert check_suppression(db_session, "picky@example.com", "BILL", is_exempt=False) is not None
    assert check_suppression(db_session, "picky@example.com", "VOICE", is_exempt=False) is None


def test_domain_scoped_suppression_is_overridden_by_exempt_templates(db_session):
    add_suppression(
        db_session, recipient_email="picky2@example.com", domain="BILL", reason=SuppressionReason.MANUAL_UNSUBSCRIBE
    )
    assert check_suppression(db_session, "picky2@example.com", "BILL", is_exempt=True) is None


# --- send_notification wiring ---

def test_hard_bounced_address_suppresses_send(db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch)
    account_id = _account(db_session, "Bounce Wired Co")
    add_suppression(
        db_session, recipient_email="wired-bounce@example.com", domain=None, reason=SuppressionReason.HARD_BOUNCE
    )

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="wired-bounce@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Bounce Wired Co", "user_display_name": "wired-bounce@example.com",
        },
    )
    assert delivery.status == NotificationDeliveryStatus.SUPPRESSED
    assert "hard_bounce" in delivery.error
    assert sent == []


def test_hard_bounced_address_suppresses_even_critical_send(db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch)
    account_id = _account(db_session, "Bounce Critical Co")
    add_suppression(
        db_session, recipient_email="wired-bounce2@example.com", domain=None, reason=SuppressionReason.HARD_BOUNCE
    )

    # number.released is CRITICAL priority - normally bypasses opt-out, but not a hard bounce.
    delivery = send_notification(
        db_session, event_name="number.released", account_id=account_id,
        recipient_email="wired-bounce2@example.com",
        context={
            "number_formatted": "+15550001111", "user_display_name": "wired-bounce2@example.com",
            "release_completed_at": "2026-08-10",
        },
    )
    assert delivery.status == NotificationDeliveryStatus.SUPPRESSED
    assert sent == []


def test_disabled_domain_preference_suppresses_matching_template(db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch)
    account_id = _account(db_session, "Domain Pref Co")
    update_preference(db_session, account_id, disabled_domains=["NUM"])

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="domainpref@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Domain Pref Co", "user_display_name": "domainpref@example.com",
        },
    )
    assert delivery.status == NotificationDeliveryStatus.SUPPRESSED
    assert sent == []


def test_disabled_domain_preference_does_not_affect_other_domains(db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch)
    account_id = _account(db_session, "Domain Pref Other Co")
    update_preference(db_session, account_id, disabled_domains=["BILL"])

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="domainpref2@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Domain Pref Other Co", "user_display_name": "domainpref2@example.com",
        },
    )
    assert delivery.status == NotificationDeliveryStatus.SENT
    assert len(sent) == 1


def test_successful_send_stores_provider_message_id(db_session, monkeypatch):
    _stub_send_email(monkeypatch, message_id="resend_abc123")
    account_id = _account(db_session, "Message Id Co")

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="msgid@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Message Id Co", "user_display_name": "msgid@example.com",
        },
    )
    assert delivery.provider_message_id == "resend_abc123"


def test_non_exempt_send_appends_unsubscribe_footer(db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch)
    account_id = _account(db_session, "Footer Co")

    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="footer@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Footer Co", "user_display_name": "footer@example.com",
        },
    )
    assert len(sent) == 1
    assert "/notifications/unsubscribe?token=" in sent[0]["body"]


def test_exempt_send_has_no_unsubscribe_footer(db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch)
    account_id = _account(db_session, "No Footer Co")

    # auth.account_activated is SECURITY category.
    send_notification(
        db_session, event_name="auth.account_activated", account_id=account_id,
        recipient_email="nofooter@example.com", context={"user_display_name": "nofooter@example.com"},
    )
    assert len(sent) == 1
    assert "unsubscribe" not in sent[0]["body"].lower()


# --- one-click unsubscribe ---

def test_unsubscribe_via_token_adds_domain_scoped_suppression(db_session):
    token = _create_unsubscribe_token("unsub@example.com", "BILL")
    success, message = unsubscribe_via_token(db_session, token)
    assert success is True
    assert "BILL" in message

    assert check_suppression(db_session, "unsub@example.com", "BILL", is_exempt=False) is not None
    assert check_suppression(db_session, "unsub@example.com", "VOICE", is_exempt=False) is None


def test_unsubscribe_via_invalid_token_fails(db_session):
    success, message = unsubscribe_via_token(db_session, "not-a-real-token")
    assert success is False


def test_decode_unsubscribe_token_rejects_a_login_token():
    """A JWT minted for a different purpose (e.g. login) must never be
    accepted here - the scope claim is what keeps token types apart."""
    from app.core.security import create_access_token

    login_token = create_access_token(subject="some-account-id", scope="customer")
    assert decode_unsubscribe_token(login_token) is None


def test_unsubscribe_endpoint_confirms_via_html(client, db_session):
    token = _create_unsubscribe_token("routeunsub@example.com", "VOICE")
    response = client.get(f"/notifications/unsubscribe?token={token}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "unsubscribed" in response.text.lower()


def test_unsubscribe_endpoint_rejects_bad_token(client):
    response = client.get("/notifications/unsubscribe?token=garbage")
    assert response.status_code == 400


# --- Resend webhook ---

_TEST_WEBHOOK_SECRET = "whsec_" + base64.b64encode(b"test-signing-secret-1234").decode()


def _resend_signature(secret: str, svix_id: str, svix_timestamp: str, body: bytes) -> str:
    secret_bytes = base64.b64decode(secret.split("_", 1)[1])
    signed_content = f"{svix_id}.{svix_timestamp}.{body.decode()}".encode()
    sig = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def test_resend_webhook_rejects_missing_signature(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_webhook_secret", _TEST_WEBHOOK_SECRET)
    response = client.post("/notifications/webhooks/resend", json={"type": "email.delivered", "data": {}})
    assert response.status_code == 401


def test_resend_webhook_rejects_wrong_signature(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_webhook_secret", _TEST_WEBHOOK_SECRET)
    body = json.dumps({"type": "email.delivered", "data": {}}).encode()
    response = client.post(
        "/notifications/webhooks/resend",
        content=body,
        headers={
            "svix-id": "msg_1", "svix-timestamp": "1700000000", "svix-signature": "v1,not-a-real-signature",
            "content-type": "application/json",
        },
    )
    assert response.status_code == 401


def test_resend_webhook_updates_delivery_status_on_bounce(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_webhook_secret", _TEST_WEBHOOK_SECRET)
    _stub_send_email(monkeypatch, message_id="resend_bounce_target")
    account_id = _account(db_session, "Webhook Bounce Co")

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="webhookbounce@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Webhook Bounce Co", "user_display_name": "webhookbounce@example.com",
        },
    )
    assert delivery.provider_message_id == "resend_bounce_target"

    body = json.dumps(
        {"type": "email.bounced", "data": {"email_id": "resend_bounce_target", "to": ["webhookbounce@example.com"]}}
    ).encode()
    svix_id, svix_timestamp = "msg_2", "1700000001"
    signature = _resend_signature(_TEST_WEBHOOK_SECRET, svix_id, svix_timestamp, body)

    response = client.post(
        "/notifications/webhooks/resend",
        content=body,
        headers={
            "svix-id": svix_id, "svix-timestamp": svix_timestamp, "svix-signature": signature,
            "content-type": "application/json",
        },
    )
    assert response.status_code == 204

    db_session.refresh(delivery)
    assert delivery.status == NotificationDeliveryStatus.BOUNCED
    assert check_suppression(db_session, "webhookbounce@example.com", None, is_exempt=False) is not None


def test_resend_webhook_delivered_updates_status(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_webhook_secret", _TEST_WEBHOOK_SECRET)
    _stub_send_email(monkeypatch, message_id="resend_delivered_target")
    account_id = _account(db_session, "Webhook Delivered Co")

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="webhookdelivered@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Webhook Delivered Co", "user_display_name": "webhookdelivered@example.com",
        },
    )

    body = json.dumps(
        {"type": "email.delivered", "data": {"email_id": "resend_delivered_target", "to": ["webhookdelivered@example.com"]}}
    ).encode()
    svix_id, svix_timestamp = "msg_3", "1700000002"
    signature = _resend_signature(_TEST_WEBHOOK_SECRET, svix_id, svix_timestamp, body)

    response = client.post(
        "/notifications/webhooks/resend",
        content=body,
        headers={
            "svix-id": svix_id, "svix-timestamp": svix_timestamp, "svix-signature": signature,
            "content-type": "application/json",
        },
    )
    assert response.status_code == 204

    db_session.refresh(delivery)
    assert delivery.status == NotificationDeliveryStatus.DELIVERED
    # A delivered event is not a suppression signal.
    assert check_suppression(db_session, "webhookdelivered@example.com", None, is_exempt=False) is None


# --- staff visibility + preferences route ---

def test_staff_can_list_suppressions(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    add_suppression(
        db_session, recipient_email="staffvisible@example.com", domain=None, reason=SuppressionReason.HARD_BOUNCE
    )

    staff_service.create_staff(
        db_session, email="commsstaff1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    token = client.post(
        "/staff/login", json={"email": "commsstaff1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get(
        "/notifications/staff/suppressions", params={"recipient_email": "staffvisible@example.com"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["reason"] == "hard_bounce"


def test_put_preferences_updates_disabled_domains(client):
    client.post(
        "/auth/signup",
        json={
            "account_name": "Disabled Domains Co", "account_type": "business",
            "email": "disableddomains@example.com", "password": "supersecret123",
        },
    )
    token = client.post(
        "/auth/login", json={"email": "disableddomains@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/notifications/preferences", json={"disabled_domains": ["BILL", "VOICE"]}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert sorted(response.json()["disabled_domains"]) == ["BILL", "VOICE"]

    refetched = client.get("/notifications/preferences", headers=headers).json()
    assert sorted(refetched["disabled_domains"]) == ["BILL", "VOICE"]
