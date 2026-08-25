from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={"account_name": "SMS Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    account_id = signup.json()["account_id"]
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (sending SMS) for
    # a TRIALING account, and this file's tests are about SMS mechanics,
    # not trial-gating, so upgrade to a real paid plan here rather than
    # adding this to every individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token, account_id


def _make_number(db_session, account_id: str, e164: str, sms_enabled: bool) -> PhoneNumber:
    number = PhoneNumber(
        e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id, sms_enabled=sms_enabled
    )
    db_session.add(number)
    db_session.commit()
    db_session.refresh(number)
    return number


def test_sms_send_requires_sms_enabled(client, db_session):
    token, account_id = _signup_and_login(client, "sms-notenabled1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550011111", sms_enabled=False)

    response = client.post(
        "/messaging/sms/send",
        json={"phone_number_id": number.id, "to": "+15559998888", "body": "hi"},
        headers=headers,
    )
    assert response.status_code == 400


def test_sms_send_and_list_conversation(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "sms-send1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550022222", sms_enabled=True)

    monkeypatch.setattr(
        "app.messaging.service.telecom.send_customer_sms",
        lambda **kwargs: {"sid": "SMsmsout1", "status": "queued"},
    )
    sent = client.post(
        "/messaging/sms/send",
        json={"phone_number_id": number.id, "to": "+15557778888", "body": "Hello via SMS"},
        headers=headers,
    )
    assert sent.status_code == 201
    assert sent.json()["status"] == "queued"

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert len(conversations) == 1
    assert conversations[0]["channel"] == "sms"
    assert conversations[0]["customer_number"] == "+15557778888"


def test_sms_inbound_message_creates_conversation(client, db_session):
    token, account_id = _signup_and_login(client, "sms-inbound1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _make_number(db_session, account_id, "+15550033333", sms_enabled=True)

    url = "http://testserver/messaging/sms/incoming"
    params = {"To": "+15550033333", "From": "+15554443333", "Body": "Do you deliver?", "MessageSid": "SMsmsin1"}
    sig = _twilio_signature(url, params)
    response = client.post("/messaging/sms/incoming", data=params, headers={"X-Twilio-Signature": sig})
    assert response.status_code == 204

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert len(conversations) == 1
    messages = client.get(f"/messaging/conversations/{conversations[0]['id']}/messages", headers=headers).json()
    assert messages[0]["direction"] == "inbound"
    assert messages[0]["body"] == "Do you deliver?"


def test_sms_stop_keyword_opts_out_and_blocks_future_sends(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "sms-optout1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550044444", sms_enabled=True)

    url = "http://testserver/messaging/sms/incoming"
    params = {"To": "+15550044444", "From": "+15556665555", "Body": "STOP", "MessageSid": "SMsmsstop1"}
    sig = _twilio_signature(url, params)
    client.post("/messaging/sms/incoming", data=params, headers={"X-Twilio-Signature": sig})

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert conversations[0]["opted_out"] is True

    monkeypatch.setattr(
        "app.messaging.service.telecom.send_customer_sms",
        lambda **kwargs: {"sid": "SMshouldnotsend", "status": "queued"},
    )
    blocked = client.post(
        "/messaging/sms/send",
        json={"phone_number_id": number.id, "to": "+15556665555", "body": "Still there?"},
        headers=headers,
    )
    assert blocked.status_code == 409


def test_whatsapp_and_sms_conversations_stay_separate(client, db_session, monkeypatch):
    """Same customer number, same business number, but the two channels
    are distinct threads - whatsapp_enabled=False here should not block SMS."""
    token, account_id = _signup_and_login(client, "dual-channel1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550055555", sms_enabled=True)

    monkeypatch.setattr(
        "app.messaging.service.telecom.send_customer_sms",
        lambda **kwargs: {"sid": "SMdual1", "status": "queued"},
    )
    sent = client.post(
        "/messaging/sms/send",
        json={"phone_number_id": number.id, "to": "+15559990000", "body": "sms channel"},
        headers=headers,
    )
    assert sent.status_code == 201

    blocked_whatsapp = client.post(
        "/messaging/whatsapp/send",
        json={"phone_number_id": number.id, "to": "+15559990000", "body": "whatsapp channel"},
        headers=headers,
    )
    assert blocked_whatsapp.status_code == 400

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert len(conversations) == 1
    assert conversations[0]["channel"] == "sms"
