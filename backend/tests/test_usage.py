from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Usage Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _place_outbound_call(client, db_session, monkeypatch, token, account_id, e164, to, call_sid):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": call_sid, "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    number = PhoneNumber(e164=e164, country="GB", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()

    response = client.post(
        "/media/voice/outbound",
        json={"to": to, "from": e164},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text


def test_completed_call_records_a_usage_event(client, db_session, monkeypatch):
    token = _signup_and_login(client, "usagecall@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550002222", "+15559990000", "CAusage1")

    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAusage1", "CallStatus": "completed", "CallDuration": "125"}
    signature = _twilio_signature(callback_url, callback_params)
    callback_response = client.post(
        "/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature}
    )
    assert callback_response.status_code == 204

    usage_response = client.get("/usage", headers={"Authorization": f"Bearer {token}"})
    assert usage_response.status_code == 200
    events = usage_response.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "call_seconds"
    assert events[0]["quantity"] == 125
    assert events[0]["unit"] == "seconds"
    assert events[0]["country_band"] == "GB"


def test_duplicate_status_callback_does_not_double_count_usage(client, db_session, monkeypatch):
    token = _signup_and_login(client, "usagedupe@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550003333", "+15559990001", "CAusage2")

    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAusage2", "CallStatus": "completed", "CallDuration": "60"}
    signature = _twilio_signature(callback_url, callback_params)

    for _ in range(2):
        response = client.post(
            "/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature}
        )
        assert response.status_code == 204

    usage_response = client.get("/usage", headers={"Authorization": f"Bearer {token}"})
    assert len(usage_response.json()) == 1


def test_usage_requires_admin(client):
    owner_token = _signup_and_login(client, "usagememberowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "usagemembermember@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "usagemembermember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.get("/usage", headers={"Authorization": f"Bearer {member_token}"})
    assert response.status_code == 403
