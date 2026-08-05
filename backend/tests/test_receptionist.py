import pytest
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
    # No AI-generated summary at all without consent - nothing to flag.
    assert calls[0]["guardrail_flags"] == []


@pytest.mark.live
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
    # Real Groq output on an ordinary message - nothing here should trip
    # the pricing/legal/medical guardrail.
    assert calls[0]["guardrail_flags"] == []


def test_receptionist_call_degrades_to_raw_transcript_when_groq_is_down(client, db_session, monkeypatch):
    """Chaos test proving the documented degrade behavior in
    media.service.capture_receptionist_call actually works: a genuine Groq
    outage mid-call (LLMError, not a missing API key) must still capture
    the raw transcript and return a normal TwiML response - never break the
    live call."""
    from app.integrations.llm.groq import LLMError

    token, account_id = _signup_and_login(client, "receptionistgroqdown@example.com")
    number = PhoneNumber(
        e164="+15550099999", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    def _raise(*args, **kwargs):
        raise LLMError("Groq qualification extraction failed: connection timed out")

    monkeypatch.setattr("app.media.service.qualify_caller", _raise)

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecgroqdown1", "To": "+15550099999", "From": "+15559999999",
        "SpeechResult": "Hi this is Sam calling about a delayed order, please call back.",
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Hangup" in response.text or "<Dial" not in response.text

    calls = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(calls) == 1
    assert calls[0]["raw_transcript"].startswith("Hi this is Sam")
    assert calls[0]["caller_name"] is None
    assert calls[0]["urgency"] is None
    assert calls[0]["model_version"] is None
    assert calls[0]["is_likely_spam"] is False


def test_receptionist_call_flags_a_pricing_commitment_in_the_generated_summary(client, db_session, monkeypatch):
    """The system prompt already tells the model never to quote prices -
    this proves the guardrail catches it anyway if the model does it,
    rather than trusting the prompt alone."""
    token, account_id = _signup_and_login(client, "receptionistguardrail1@example.com")
    number = PhoneNumber(
        e164="+15550055555", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )
    monkeypatch.setattr(
        "app.media.service.qualify_caller",
        lambda db, account_id, transcript, jurisdiction=None: (
            {
                "name": "Sam",
                "company": None,
                "reason": "asked for a repair quote",
                "summary": "Sam called and was told the repair would cost $75.",
                "urgency": "low",
                "callback_preference": None,
            },
            "groq/test",
        ),
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecguard1", "To": "+15550055555", "From": "+15559995555",
        "SpeechResult": "Hi, how much would a repair cost?",
    }
    signature = _twilio_signature(url, params)
    client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})

    calls = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(calls) == 1
    assert calls[0]["guardrail_flags"] == ["pricing_commitment"]


def test_receptionist_call_flags_a_likely_spam_transcript(client, db_session, monkeypatch):
    """AI content signal (Roadmap 'AI-driven fraud/spam signals') - distinct
    from the platform-wide inbound-velocity signal on CallRecord, this comes
    straight from qualify_caller()'s own extraction, same as name/urgency."""
    token, account_id = _signup_and_login(client, "receptionistspam1@example.com")
    number = PhoneNumber(
        e164="+15550077777", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )
    monkeypatch.setattr(
        "app.media.service.qualify_caller",
        lambda db, account_id, transcript, jurisdiction=None: (
            {
                "name": None,
                "company": None,
                "reason": "extended vehicle warranty offer",
                "summary": "An automated pitch offering an extended vehicle warranty, unrelated to this business.",
                "urgency": "low",
                "callback_preference": None,
                "is_likely_spam": True,
                "spam_reason": "extended warranty scam pitch",
            },
            "groq/test",
        ),
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecspam1", "To": "+15550077777", "From": "+15559997777",
        "SpeechResult": "Your car's extended warranty is about to expire, press one to renew",
    }
    signature = _twilio_signature(url, params)
    client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})

    calls = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(calls) == 1
    assert calls[0]["is_likely_spam"] is True
    assert calls[0]["spam_reason"] == "extended warranty scam pitch"


def test_receptionist_call_is_not_flagged_spam_by_default(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "receptionistspam2@example.com")
    number = PhoneNumber(
        e164="+15550088877", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )
    monkeypatch.setattr(
        "app.media.service.qualify_caller",
        lambda db, account_id, transcript, jurisdiction=None: (
            {
                "name": "Sam",
                "company": "Acme",
                "reason": "asked about business hours",
                "summary": "Sam from Acme asked when the shop opens tomorrow.",
                "urgency": "low",
                "callback_preference": None,
                "is_likely_spam": False,
                "spam_reason": None,
            },
            "groq/test",
        ),
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecspam2", "To": "+15550088877", "From": "+15559998877",
        "SpeechResult": "Hi, what time do you open tomorrow?",
    }
    signature = _twilio_signature(url, params)
    client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})

    calls = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(calls) == 1
    assert calls[0]["is_likely_spam"] is False
    assert calls[0]["spam_reason"] is None


def test_receptionist_call_has_no_guardrail_flags_when_summary_is_clean(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "receptionistguardrail2@example.com")
    number = PhoneNumber(
        e164="+15550066666", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )
    monkeypatch.setattr(
        "app.media.service.qualify_caller",
        lambda db, account_id, transcript, jurisdiction=None: (
            {
                "name": "Sam",
                "company": None,
                "reason": "asked for a repair quote",
                "summary": "Sam called asking about repair pricing and would like a callback.",
                "urgency": "low",
                "callback_preference": None,
            },
            "groq/test",
        ),
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecguard2", "To": "+15550066666", "From": "+15559996666",
        "SpeechResult": "Hi, how much would a repair cost?",
    }
    signature = _twilio_signature(url, params)
    client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})

    calls = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert len(calls) == 1
    assert calls[0]["guardrail_flags"] == []


@pytest.mark.live
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


def _capture_a_call(client, db_session, account_id: str, *, e164: str, call_sid: str) -> str:
    number = PhoneNumber(
        e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": call_sid, "To": e164, "From": "+15559990000",
        "SpeechResult": "Hi, calling about a quote, please call back.",
    }
    signature = _twilio_signature(url, params)
    client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    return number.id


def test_assign_receptionist_call_routes_it_to_a_team_member(client, db_session):
    owner_token, account_id = _signup_and_login(client, "receptionistroute1@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _capture_a_call(client, db_session, account_id, e164="+15550055555", call_sid="CArecroute1")

    client.post(
        "/team/members",
        json={"email": "receptionistrouteteammate1@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    members = client.get("/team/members", headers=owner_headers).json()
    teammate_id = next(m["id"] for m in members if m["email"] == "receptionistrouteteammate1@example.com")

    calls = client.get("/media/receptionist/calls", headers=owner_headers).json()
    call_id = calls[0]["id"]
    assert calls[0]["assigned_user_id"] is None

    response = client.post(
        f"/media/receptionist/calls/{call_id}/assign",
        json={"assigned_user_id": teammate_id},
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["assigned_user_id"] == teammate_id

    calls_after = client.get("/media/receptionist/calls", headers=owner_headers).json()
    assert calls_after[0]["assigned_user_id"] == teammate_id
    assert calls_after[0]["assigned_user_email"] == "receptionistrouteteammate1@example.com"


def test_assign_receptionist_call_rejects_a_nominee_outside_the_account(client, db_session):
    owner_token, account_id = _signup_and_login(client, "receptionistroute2@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _capture_a_call(client, db_session, account_id, e164="+15550066666", call_sid="CArecroute2")

    other_token, _ = _signup_and_login(client, "receptionistroute2b@example.com")
    other_user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {other_token}"}).json()["id"]

    calls = client.get("/media/receptionist/calls", headers=owner_headers).json()
    call_id = calls[0]["id"]

    response = client.post(
        f"/media/receptionist/calls/{call_id}/assign",
        json={"assigned_user_id": other_user_id},
        headers=owner_headers,
    )
    assert response.status_code == 403
    assert "no team member" in response.json()["detail"].lower()


def test_assign_receptionist_call_rejects_other_accounts_call(client, db_session):
    owner_token, account_id = _signup_and_login(client, "receptionistroute3@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _capture_a_call(client, db_session, account_id, e164="+15550077777", call_sid="CArecroute3")
    calls = client.get("/media/receptionist/calls", headers=owner_headers).json()
    call_id = calls[0]["id"]

    intruder_token, _ = _signup_and_login(client, "receptionistroute3b@example.com")
    response = client.post(
        f"/media/receptionist/calls/{call_id}/assign",
        json={"assigned_user_id": None},
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_assign_receptionist_call_can_unassign(client, db_session):
    owner_token, account_id = _signup_and_login(client, "receptionistroute4@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _capture_a_call(client, db_session, account_id, e164="+15550088888", call_sid="CArecroute4")

    owner_me = client.get("/auth/me", headers=owner_headers).json()
    calls = client.get("/media/receptionist/calls", headers=owner_headers).json()
    call_id = calls[0]["id"]

    client.post(
        f"/media/receptionist/calls/{call_id}/assign",
        json={"assigned_user_id": owner_me["id"]},
        headers=owner_headers,
    )
    response = client.post(
        f"/media/receptionist/calls/{call_id}/assign",
        json={"assigned_user_id": None},
        headers=owner_headers,
    )
    assert response.status_code == 200
    assert response.json()["assigned_user_id"] is None


def _seed_receptionist_call_with_summary(db_session, account_id: str, *, e164: str, summary: str):
    from app.media.models import ReceptionistCall

    number = PhoneNumber(
        e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    call = ReceptionistCall(
        account_id=account_id, phone_number_id=number.id, call_sid=f"CAseed{e164}",
        caller_number="+15559990000", raw_transcript="Hi, calling about a quote.",
        summary=summary, model_version="groq/test",
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_edit_receptionist_call_summary_preserves_original(client, db_session):
    owner_token, account_id = _signup_and_login(client, "receptionistedit1@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    call = _seed_receptionist_call_with_summary(
        db_session, account_id, e164="+15550099999", summary="Original AI summary."
    )
    call_id = call.id

    response = client.patch(
        f"/media/receptionist/calls/{call_id}",
        json={"summary": "Corrected: caller wants a refund, not a quote."},
        headers=owner_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "Corrected: caller wants a refund, not a quote."
    assert response.json()["original_summary"] is not None

    calls = client.get("/media/receptionist/calls", headers=owner_headers).json()
    assert calls[0]["edited_at"] is not None


def test_edit_receptionist_call_summary_rejects_other_account(client, db_session):
    owner_token, account_id = _signup_and_login(client, "receptionistedit2owner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _capture_a_call(client, db_session, account_id, e164="+15550011122", call_sid="CArecedit2")
    call_id = client.get("/media/receptionist/calls", headers=owner_headers).json()[0]["id"]

    intruder_token, _ = _signup_and_login(client, "receptionistedit2intruder@example.com")
    response = client.patch(
        f"/media/receptionist/calls/{call_id}",
        json={"summary": "Hijacked."},
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_edit_receptionist_call_summary_requires_auth(client, db_session):
    _, account_id = _signup_and_login(client, "receptionistedit3@example.com")
    _capture_a_call(client, db_session, account_id, e164="+15550033344", call_sid="CArecedit3")

    response = client.patch("/media/receptionist/calls/does-not-matter", json={"summary": "x"})
    assert response.status_code == 401
