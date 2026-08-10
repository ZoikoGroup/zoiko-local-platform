from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={"account_name": "Messaging Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    account_id = signup.json()["account_id"]
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    return token, account_id


def _make_number(db_session, account_id: str, e164: str, whatsapp_enabled: bool) -> PhoneNumber:
    number = PhoneNumber(
        e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id, whatsapp_enabled=whatsapp_enabled
    )
    db_session.add(number)
    db_session.commit()
    db_session.refresh(number)
    return number


def test_send_requires_number_ownership(client, db_session):
    token, _ = _signup_and_login(client, "wa-owner1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    response = client.post(
        "/messaging/whatsapp/send",
        json={"phone_number_id": "00000000-0000-0000-0000-000000000000", "to": "+15551234567", "body": "hi"},
        headers=headers,
    )
    assert response.status_code == 404


def test_send_requires_whatsapp_enabled(client, db_session):
    token, account_id = _signup_and_login(client, "wa-notenabled1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550001111", whatsapp_enabled=False)

    response = client.post(
        "/messaging/whatsapp/send",
        json={"phone_number_id": number.id, "to": "+15559998888", "body": "hi"},
        headers=headers,
    )
    assert response.status_code == 400


def test_send_message_and_list_conversation(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "wa-send1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550002222", whatsapp_enabled=True)

    monkeypatch.setattr(
        "app.messaging.service.telecom.send_whatsapp_message",
        lambda **kwargs: {"sid": "SMoutbound1", "status": "queued"},
    )
    sent = client.post(
        "/messaging/whatsapp/send",
        json={"phone_number_id": number.id, "to": "+15557778888", "body": "Hello from Zoiko Local"},
        headers=headers,
    )
    assert sent.status_code == 201
    assert sent.json()["status"] == "queued"

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert len(conversations) == 1
    assert conversations[0]["customer_number"] == "+15557778888"
    assert conversations[0]["opted_out"] is False

    messages = client.get(f"/messaging/conversations/{conversations[0]['id']}/messages", headers=headers).json()
    assert len(messages) == 1
    assert messages[0]["direction"] == "outbound"
    assert messages[0]["body"] == "Hello from Zoiko Local"


def test_inbound_message_creates_conversation(client, db_session):
    token, account_id = _signup_and_login(client, "wa-inbound1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _make_number(db_session, account_id, "+15550003333", whatsapp_enabled=True)

    url = "http://testserver/messaging/whatsapp/incoming"
    params = {
        "To": "whatsapp:+15550003333", "From": "whatsapp:+15554443333",
        "Body": "What are your hours?", "MessageSid": "SMinbound1",
    }
    sig = _twilio_signature(url, params)
    response = client.post("/messaging/whatsapp/incoming", data=params, headers={"X-Twilio-Signature": sig})
    assert response.status_code == 204

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert len(conversations) == 1
    assert conversations[0]["customer_number"] == "+15554443333"

    messages = client.get(f"/messaging/conversations/{conversations[0]['id']}/messages", headers=headers).json()
    assert len(messages) == 1
    assert messages[0]["direction"] == "inbound"
    assert messages[0]["body"] == "What are your hours?"


def test_stop_keyword_opts_out_and_blocks_future_sends(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "wa-optout1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550004444", whatsapp_enabled=True)

    url = "http://testserver/messaging/whatsapp/incoming"
    params = {"To": "whatsapp:+15550004444", "From": "whatsapp:+15556665555", "Body": "STOP", "MessageSid": "SMstop1"}
    sig = _twilio_signature(url, params)
    client.post("/messaging/whatsapp/incoming", data=params, headers={"X-Twilio-Signature": sig})

    conversations = client.get("/messaging/conversations", headers=headers).json()
    assert conversations[0]["opted_out"] is True

    monkeypatch.setattr(
        "app.messaging.service.telecom.send_whatsapp_message",
        lambda **kwargs: {"sid": "SMshouldnotsend", "status": "queued"},
    )
    blocked = client.post(
        "/messaging/whatsapp/send",
        json={"phone_number_id": number.id, "to": "+15556665555", "body": "Still there?"},
        headers=headers,
    )
    assert blocked.status_code == 409


def test_status_callback_updates_message_status(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "wa-status1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550005555", whatsapp_enabled=True)

    monkeypatch.setattr(
        "app.messaging.service.telecom.send_whatsapp_message",
        lambda **kwargs: {"sid": "SMstatus1", "status": "queued"},
    )
    client.post(
        "/messaging/whatsapp/send",
        json={"phone_number_id": number.id, "to": "+15559990000", "body": "hi"},
        headers=headers,
    )

    url = "http://testserver/messaging/whatsapp/status"
    params = {"MessageSid": "SMstatus1", "MessageStatus": "delivered"}
    sig = _twilio_signature(url, params)
    response = client.post("/messaging/whatsapp/status", data=params, headers={"X-Twilio-Signature": sig})
    assert response.status_code == 204

    conversations = client.get("/messaging/conversations", headers=headers).json()
    messages = client.get(f"/messaging/conversations/{conversations[0]['id']}/messages", headers=headers).json()
    assert messages[0]["status"] == "delivered"
