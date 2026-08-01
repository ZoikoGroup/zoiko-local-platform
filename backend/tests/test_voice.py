from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Voice Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_outbound_call_requires_auth(client):
    response = client.post(
        "/media/voice/outbound", json={"to": "+15551234567", "from": "+15550001111"}
    )
    assert response.status_code == 401


def test_outbound_call_rejects_number_not_owned_by_account(client):
    token = _signup_and_login(client, "voiceowner@example.com")
    response = client.post(
        "/media/voice/outbound",
        json={"to": "+15551234567", "from": "+15559999999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_incoming_webhook_rejects_missing_signature(client):
    response = client.post(
        "/media/voice/incoming",
        data={"To": "+15550001111", "From": "+15551234567", "CallSid": "CA123", "CallStatus": "ringing"},
    )
    assert response.status_code == 403


def test_incoming_webhook_rejects_invalid_signature(client):
    response = client.post(
        "/media/voice/incoming",
        data={"To": "+15550001111", "From": "+15551234567", "CallSid": "CA123", "CallStatus": "ringing"},
        headers={"X-Twilio-Signature": "not-a-real-signature"},
    )
    assert response.status_code == 403


def test_list_calls_requires_auth(client):
    response = client.get("/media/voice/calls")
    assert response.status_code == 401


def test_status_callback_updates_call_duration(client, db_session):
    token = _signup_and_login(client, "voicestatus@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550007777", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550007777", "From": "+15559990000", "CallSid": "CAxyz123", "CallStatus": "ringing",
    }
    incoming_signature = _twilio_signature(incoming_url, incoming_params)
    incoming_response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": incoming_signature}
    )
    assert incoming_response.status_code == 200

    status_url = "http://testserver/media/voice/status-callback"
    status_params = {"CallSid": "CAxyz123", "CallStatus": "completed", "CallDuration": "42"}
    status_signature = _twilio_signature(status_url, status_params)
    status_response = client.post(
        "/media/voice/status-callback", data=status_params, headers={"X-Twilio-Signature": status_signature}
    )
    assert status_response.status_code == 204

    calls_response = client.get("/media/voice/calls", headers={"Authorization": f"Bearer {token}"})
    assert calls_response.status_code == 200
    calls = calls_response.json()
    assert len(calls) == 1
    assert calls[0]["sid"] == "CAxyz123"
    assert calls[0]["status"] == "completed"
    assert calls[0]["duration"] == 42


def test_incoming_call_forwards_when_configured(client, db_session):
    token = _signup_and_login(client, "voiceforward@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550008888", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    routing_response = client.put(
        "/numbers/+15550008888/routing",
        json={"forwarding_number": "+15551112222"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert routing_response.status_code == 200
    assert routing_response.json()["forwarding_number"] == "+15551112222"

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550008888", "From": "+15559990000", "CallSid": "CAforward1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Dial" in response.text
    assert "+15551112222" in response.text


def test_forwarded_call_twiml_requests_recording(client, db_session):
    token = _signup_and_login(client, "voicerecord@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550001234", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    client.put(
        "/numbers/+15550001234/routing",
        json={"forwarding_number": "+15551112222"},
        headers={"Authorization": f"Bearer {token}"},
    )

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550001234", "From": "+15559990000", "CallSid": "CArecord1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert 'record="record-from-answer-dual"' in response.text
    assert "media/voice/recording-callback" in response.text


def test_recording_callback_rejects_missing_signature(client):
    response = client.post(
        "/media/voice/recording-callback",
        data={"CallSid": "CArecord2", "RecordingUrl": "https://example.com/rec.wav", "RecordingDuration": "30"},
    )
    assert response.status_code == 403


def test_recording_callback_attaches_recording_to_the_call(client, db_session):
    token = _signup_and_login(client, "voicerecordcb@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550005678", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550005678", "From": "+15559990000", "CallSid": "CArecord3", "CallStatus": "ringing",
    }
    incoming_signature = _twilio_signature(incoming_url, incoming_params)
    client.post("/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": incoming_signature})

    recording_url = "http://testserver/media/voice/recording-callback"
    recording_params = {
        "CallSid": "CArecord3", "RecordingUrl": "https://example.com/rec.wav", "RecordingDuration": "58",
    }
    recording_signature = _twilio_signature(recording_url, recording_params)
    recording_response = client.post(
        "/media/voice/recording-callback", data=recording_params,
        headers={"X-Twilio-Signature": recording_signature},
    )
    assert recording_response.status_code == 204

    calls = client.get("/media/voice/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert calls[0]["recording_url"] == "https://example.com/rec.wav"
    assert calls[0]["duration"] == 58


def test_incoming_call_goes_to_voicemail_outside_business_hours(client, db_session):
    token = _signup_and_login(client, "voicehours@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550009999", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    # a window guaranteed to exclude "now": 1 minute long, ending 2 hours ago
    from datetime import datetime, timedelta, timezone

    end_dt = datetime.now(timezone.utc) - timedelta(hours=2)
    start_dt = end_dt - timedelta(minutes=1)
    routing_response = client.put(
        "/numbers/+15550009999/routing",
        json={
            "forwarding_number": "+15551112222",
            "business_hours_start": start_dt.strftime("%H:%M:%S"),
            "business_hours_end": end_dt.strftime("%H:%M:%S"),
            "business_hours_timezone": "UTC",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert routing_response.status_code == 200

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550009999", "From": "+15559990000", "CallSid": "CAhours1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Record" in response.text
    assert "<Dial" not in response.text
