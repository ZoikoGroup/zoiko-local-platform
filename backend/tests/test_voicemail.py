from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def test_recording_complete_rejects_missing_signature(client):
    response = client.post(
        "/media/voicemail/recording-complete",
        data={"To": "+15550001111", "From": "+15559999999", "RecordingUrl": "https://example.com/r.mp3"},
    )
    assert response.status_code == 403


def test_list_voicemails_requires_auth(client):
    response = client.get("/media/voicemail")
    assert response.status_code == 401


def test_recording_complete_persists_voicemail_for_owned_number(client, db_session):
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Voicemail Test Co",
            "account_type": "business",
            "email": "voicemailowner@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]

    number = PhoneNumber(
        e164="+15550003333", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    login = client.post(
        "/auth/login", json={"email": "voicemailowner@example.com", "password": "supersecret123"}
    )
    token = login.json()["access_token"]

    url = "http://testserver/media/voicemail/recording-complete"
    params = {
        "To": "+15550003333",
        "From": "+15559998888",
        "RecordingUrl": "https://example.com/recording.mp3",
        "RecordingDuration": "12",
    }
    signature = _twilio_signature(url, params)

    response = client.post(
        "/media/voicemail/recording-complete", data=params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 204

    list_response = client.get("/media/voicemail", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    voicemails = list_response.json()
    assert len(voicemails) == 1
    assert voicemails[0]["from"] == "+15559998888"
    assert voicemails[0]["duration"] == 12
