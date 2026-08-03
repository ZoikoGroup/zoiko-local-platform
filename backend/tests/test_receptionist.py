from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Receptionist Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def test_incoming_call_uses_receptionist_when_enabled(client, db_session):
    token, account_id = _signup_and_login(client, "receptionistenabled@example.com")
    number = PhoneNumber(
        e164="+15550011111", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    url = "http://testserver/media/voice/incoming"
    params = {"To": "+15550011111", "From": "+15559991111", "CallSid": "CArecep1", "CallStatus": "ringing"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/incoming", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Gather" in response.text
    assert "/media/receptionist/respond" in response.text
    # Real gap flagged and fixed: the caller must be told this is an
    # automated assistant, not a person, before anything is captured.
    assert "automated assistant" in response.text


def test_respond_without_consent_captures_raw_transcript_only(client, db_session):
    token, account_id = _signup_and_login(client, "receptionistnoconsent@example.com")
    number = PhoneNumber(
        e164="+15550022222", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecep2", "To": "+15550022222", "From": "+15559992222",
        "SpeechResult": "Hi this is Alex calling about a broken order, please call back urgently",
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Hangup" in response.text or "<Dial" not in response.text

    calls_response = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    )
    assert calls_response.status_code == 200
    calls = calls_response.json()
    assert len(calls) == 1
    assert calls[0]["raw_transcript"].startswith("Hi this is Alex")
    assert calls[0]["caller_name"] is None
    assert calls[0]["urgency"] is None
    assert calls[0]["model_version"] is None


def test_respond_with_consent_extracts_qualification_and_escalates_high_urgency(client, db_session):
    token, account_id = _signup_and_login(client, "receptionistconsent@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    owner_user_id = me.json()["id"]

    number = PhoneNumber(
        e164="+15550033333", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True, forwarding_number="+15551119999", escalation_user_id=owner_user_id,
    )
    db_session.add(number)
    db_session.commit()

    consent_response = client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert consent_response.status_code == 200

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecep3", "To": "+15550033333", "From": "+15559993333",
        "SpeechResult": (
            "Hi my name is Jordan Lee from Acme Corp, our production system is down "
            "and this is extremely urgent, please call me back right away"
        ),
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Dial" in response.text
    assert "+15551119999" in response.text

    calls_response = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    )
    calls = calls_response.json()
    assert len(calls) == 1
    assert calls[0]["caller_name"]
    assert calls[0]["urgency"] == "high"
    assert calls[0]["escalated"] is True
    assert calls[0]["model_version"]


def test_high_urgency_does_not_escalate_without_a_nominated_team_member(client, db_session):
    """forwarding_number alone (used for plain business-hours forwarding)
    must not trigger receptionist escalation - only a nominated
    escalation_user_id does (Roadmap §7: "Escalate to nominated team
    member")."""
    token, account_id = _signup_and_login(client, "receptionistnonominee@example.com")
    number = PhoneNumber(
        e164="+15550044444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True, forwarding_number="+15551119999",
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecep4", "To": "+15550044444", "From": "+15559994444",
        "SpeechResult": (
            "Hi my name is Sam Rivera, our production system is down and this is "
            "extremely urgent, please call me back right away"
        ),
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Dial" not in response.text

    calls_response = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    )
    calls = calls_response.json()
    assert len(calls) == 1
    assert calls[0]["urgency"] == "high"
    assert calls[0]["escalated"] is False


def test_receptionist_calls_requires_auth(client):
    assert client.get("/media/receptionist/calls").status_code == 401
