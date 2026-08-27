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
    token = login.json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (assigning/editing
    # a receptionist call) for a TRIALING account, and this file's tests
    # are about receptionist mechanics, not trial-gating, so upgrade to a
    # real paid plan here rather than adding this to every individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token, account_id


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


def test_escalation_uses_dedicated_phone_number_without_hijacking_calls_into_forwarding(client, db_session):
    """Confirmed live (2026-08-22): escalation used to dial forwarding_number
    unconditionally, so an AI-Receptionist-primary number (forwarding off)
    had no way to configure an escalation destination without
    forwarding_number's mere presence also flipping should_forward_call()
    to true and hijacking every inbound call into always-forward mode.
    This proves escalation_phone_number lets escalation work with
    forwarding_number left unset entirely."""
    token, account_id = _signup_and_login(client, "receptionistdedicated@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    owner_user_id = me.json()["id"]

    number = PhoneNumber(
        e164="+15550055555", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True, escalation_user_id=owner_user_id,
        escalation_phone_number="+15551119999",
    )
    db_session.add(number)
    db_session.commit()

    from app.media.service import should_forward_call
    assert should_forward_call(number) is False

    client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecep5", "To": "+15550055555", "From": "+15559995555",
        "SpeechResult": (
            "Hi my name is Taylor Kim from Beta Corp, our production system is down "
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
    assert calls[0]["escalated"] is True


def test_escalation_dial_is_recorded_when_ai_processing_consent_is_on_file(client, db_session):
    """Real gap fix: an escalated (human-handoff) call is a live two-way
    conversation with no other capture mechanism of its own - unlike the
    pre-escalation Gather utterance (already on the ReceptionistCall row)
    or a plain forwarded call (already recorded via build_ring_group_
    response). build_receptionist_reply_response previously never set
    record= on its Dial at all, so the one call category with the least
    room for missing a detail - an urgent human handoff - was the one
    that was never recorded, consent or not. Same AI_PROCESSING consent
    gate as should_record_forwarded_call, so this must be recorded once
    that consent is on file."""
    token, account_id = _signup_and_login(client, "receptionistrecorded1@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    owner_user_id = me.json()["id"]

    number = PhoneNumber(
        e164="+15550044444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True, escalation_user_id=owner_user_id,
        escalation_phone_number="+15551118888",
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
        "CallSid": "CArecrecorded1", "To": "+15550044444", "From": "+15559994444",
        "SpeechResult": (
            "Hi my name is Sam from Acme, our production system is down "
            "and this is extremely urgent, please call me back right away"
        ),
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert 'record="record-from-answer-dual"' in response.text
    assert "media/voice/recording-callback" in response.text


def test_build_receptionist_reply_response_omits_record_without_a_callback_url():
    """Same fix as above, opposite direction: no recording_callback_url
    must mean no record= at all - recording is opt-in, not a default that
    only widens what's possible once a callback URL happens to be passed.
    Unit-tested directly against the TwiML builder (not the full webhook
    flow) because escalation and recording share the same AI_PROCESSING
    consent gate in practice - there's no way to reach the escalation
    branch at all without the same consent that would also enable
    recording, so this is the only way to isolate the builder's own
    record= wiring from that consent coupling."""
    from app.integrations.telecom import twilio as telecom

    twiml = telecom.build_receptionist_reply_response(
        "Thanks, connecting you now.", forward_to="+15551117777",
        status_callback_url="http://testserver/media/voice/status-callback",
        recording_callback_url=None,
    )
    assert "<Dial" in twiml
    assert "record=" not in twiml


def test_escalation_falls_back_to_forwarding_number_when_dedicated_field_unset(client, db_session):
    """Backward compatibility: a number configured before escalation_phone_number
    existed (forwarding_number set, escalation_phone_number left null) must
    keep escalating the same way it always did."""
    token, account_id = _signup_and_login(client, "receptionistlegacyesc@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    owner_user_id = me.json()["id"]

    number = PhoneNumber(
        e164="+15550066666", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True, forwarding_number="+15551118888", escalation_user_id=owner_user_id,
    )
    db_session.add(number)
    db_session.commit()
    assert number.escalation_phone_number is None

    client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecep6", "To": "+15550066666", "From": "+15559996666",
        "SpeechResult": (
            "Hi my name is Morgan Diaz from Gamma Corp, our production system is down "
            "and this is extremely urgent, please call me back right away"
        ),
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Dial" in response.text
    assert "+15551118888" in response.text


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
    from app.billing import service as billing_service

    owner_token, account_id = _signup_and_login(client, "receptionistroute1@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    # team.members.enabled is Business+ (ZL-COM-ENT-001) - a fresh signup's
    # default free_trial plan grants no team capability.
    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")
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


def test_call_ending_records_ai_receptionist_minutes_usage_event(client, db_session):
    """Global Plans, Pricing & Commercial Launch Standard doc §5.3 - AI
    Receptionist minutes are billed at $0.39/min overage. A call that
    actually reached the AI receptionist (has a ReceptionistCall row) must
    record an ai_receptionist_minutes usage event, priced from AIUsageRate,
    once the call's final status/duration lands."""
    from app.usage.models import UsageEvent

    _, account_id = _signup_and_login(client, "receptionistusage1@example.com")
    number = PhoneNumber(
        e164="+15550066666", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    # The number has to exist and be recognized BEFORE the inbound leg (so
    # incoming_call actually routes it to the receptionist instead of the
    # "unrecognized number" branch), and update_call_status looks up an
    # existing CallRecord by call_sid, which only /media/voice/incoming
    # creates - same two-step webhook sequence as test_status_callback_
    # updates_call_duration in test_voice.py.
    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {"To": "+15550066666", "From": "+15559990000", "CallSid": "CArecusage1", "CallStatus": "ringing"}
    incoming_response = client.post(
        "/media/voice/incoming", data=incoming_params,
        headers={"X-Twilio-Signature": _twilio_signature(incoming_url, incoming_params)},
    )
    assert incoming_response.status_code == 200

    respond_url = "http://testserver/media/receptionist/respond"
    respond_params = {
        "CallSid": "CArecusage1", "To": "+15550066666", "From": "+15559990000",
        "SpeechResult": "Hi, calling about a quote, please call back.",
    }
    client.post(
        "/media/receptionist/respond", data=respond_params,
        headers={"X-Twilio-Signature": _twilio_signature(respond_url, respond_params)},
    )

    status_url = "http://testserver/media/voice/status-callback"
    status_params = {"CallSid": "CArecusage1", "CallStatus": "completed", "CallDuration": "125"}
    signature = _twilio_signature(status_url, status_params)
    response = client.post(
        "/media/voice/status-callback", data=status_params, headers={"X-Twilio-Signature": signature}
    )
    # /status-callback returns a real empty TwiML doc (200), not a bare 204 -
    # a bare 204 reaches Twilio with an empty Content-Type header (its error
    # 12300), confirmed live via a real call's own Notifications log.
    assert response.status_code == 200

    event = (
        db_session.query(UsageEvent)
        .filter(UsageEvent.event_type == "ai_receptionist_minutes")
        .order_by(UsageEvent.created_at.desc())
        .first()
    )
    assert event is not None
    assert event.account_id == account_id
    assert event.quantity == 3  # ceil(125s / 60) = 3 minutes
    assert event.estimated_cost_cents == 3 * 39


def test_ai_receptionist_minutes_bills_zero_for_a_non_billable_disposition(client, db_session):
    """Real bug fix: update_call_status used to write TWO ai_receptionist_
    minutes usage events sharing the same idempotency key - a naive one
    (plain math.ceil(duration/60), no disposition) and a disposition-aware
    one (0 for a non-billable disposition, real duration_seconds tracking).
    record_usage_event no-ops on a duplicate key, so whichever ran first
    always won - the naive one, since it was written first in the
    function - meaning the disposition-aware billing logic never actually
    took effect: even a NO-ANSWER/FAILED/BUSY/CANCELED receptionist call
    got billed its full raw duration. Confirms a NO-ANSWER call now bills
    0 minutes despite a nonzero CallDuration."""
    from app.usage.models import UsageEvent

    _, account_id = _signup_and_login(client, "receptionistusage3@example.com")
    number = PhoneNumber(
        e164="+15550088888", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {"To": "+15550088888", "From": "+15559990000", "CallSid": "CArecnoanswer1", "CallStatus": "ringing"}
    client.post(
        "/media/voice/incoming", data=incoming_params,
        headers={"X-Twilio-Signature": _twilio_signature(incoming_url, incoming_params)},
    )

    respond_url = "http://testserver/media/receptionist/respond"
    respond_params = {
        "CallSid": "CArecnoanswer1", "To": "+15550088888", "From": "+15559990000",
        "SpeechResult": "Hi, calling about a quote, please call back.",
    }
    client.post(
        "/media/receptionist/respond", data=respond_params,
        headers={"X-Twilio-Signature": _twilio_signature(respond_url, respond_params)},
    )

    status_url = "http://testserver/media/voice/status-callback"
    status_params = {"CallSid": "CArecnoanswer1", "CallStatus": "no-answer", "CallDuration": "125"}
    client.post(
        "/media/voice/status-callback", data=status_params,
        headers={"X-Twilio-Signature": _twilio_signature(status_url, status_params)},
    )

    event = (
        db_session.query(UsageEvent)
        .filter(UsageEvent.event_type == "ai_receptionist_minutes")
        .order_by(UsageEvent.created_at.desc())
        .first()
    )
    assert event is not None
    assert event.quantity == 0  # non-billable disposition, despite a 125s raw duration
    assert event.disposition == "no-answer"

    from app.media.models import ReceptionistCall

    call = db_session.query(ReceptionistCall).filter(ReceptionistCall.call_sid == "CArecnoanswer1").first()
    assert call.duration_seconds == 125  # still tracked, even though it wasn't billed


def test_call_not_reaching_receptionist_does_not_record_ai_minutes(client, db_session):
    """A plain forwarded/voicemail call must never create a
    ReceptionistCall row, so it must never be counted as AI Receptionist
    usage either - the presence of a ReceptionistCall is what
    distinguishes the two, not just that a call happened on a number with
    the flag enabled."""
    from app.usage.models import UsageEvent

    token, account_id = _signup_and_login(client, "receptionistusage2@example.com")
    number = PhoneNumber(
        e164="+15550077777", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=False,
    )
    db_session.add(number)
    db_session.commit()

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {"To": "+15550077777", "From": "+15559990000", "CallSid": "CAnorecep1", "CallStatus": "ringing"}
    client.post(
        "/media/voice/incoming", data=incoming_params,
        headers={"X-Twilio-Signature": _twilio_signature(incoming_url, incoming_params)},
    )

    status_url = "http://testserver/media/voice/status-callback"
    status_params = {"CallSid": "CAnorecep1", "CallStatus": "completed", "CallDuration": "60"}
    client.post(
        "/media/voice/status-callback", data=status_params,
        headers={"X-Twilio-Signature": _twilio_signature(status_url, status_params)},
    )

    assert db_session.query(UsageEvent).filter(UsageEvent.event_type == "ai_receptionist_minutes").count() == 0


def test_receptionist_asks_one_followup_when_name_and_reason_are_both_missing(client, db_session, monkeypatch):
    """Multi-turn conversation (Roadmap §7): a completed qualification pass
    that found neither a name nor a reason gets exactly one follow-up
    question, merged into the SAME call row (never a second one), then
    proceeds straight to the post-capture menu - never a second follow-up."""
    token, account_id = _signup_and_login(client, "receptionistfollowup1@example.com")
    number = PhoneNumber(
        e164="+15550091111", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    def _fake_qualify(db, account_id, transcript, jurisdiction=None):
        if "Jamie" in transcript:
            return (
                {
                    "name": "Jamie", "company": None, "reason": "a billing question",
                    "summary": "Jamie called with a billing question.", "urgency": "low",
                    "callback_preference": None,
                },
                "groq/test",
            )
        return (
            {
                "name": None, "company": None, "reason": None, "summary": None,
                "urgency": "low", "callback_preference": None,
            },
            "groq/test",
        )

    monkeypatch.setattr("app.media.service.qualify_caller", _fake_qualify)

    url = "http://testserver/media/receptionist/respond"
    params = {"CallSid": "CArecfollow1", "To": "+15550091111", "From": "+15559991111", "SpeechResult": "uh, hello?"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "could you tell me your name" in response.text
    assert "/media/receptionist/respond-followup" in response.text

    calls = client.get("/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(calls) == 1
    call_id = calls[0]["id"]
    assert calls[0]["caller_name"] is None

    followup_path = f"/media/receptionist/respond-followup?receptionist_call_id={call_id}"
    followup_url = f"http://testserver{followup_path}"
    followup_params = {
        "CallSid": "CArecfollow1", "To": "+15550091111", "From": "+15559991111",
        "SpeechResult": "Sorry, my name is Jamie and I have a billing question.",
    }
    followup_signature = _twilio_signature(followup_url, followup_params)
    followup_response = client.post(
        followup_path, data=followup_params, headers={"X-Twilio-Signature": followup_signature},
    )
    assert followup_response.status_code == 200
    # No second follow-up loop - proceeds straight to the post-capture menu.
    assert "could you tell me your name" not in followup_response.text
    assert "press 2" in followup_response.text.lower()

    calls = client.get("/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(calls) == 1  # still the same row, not a second one
    assert calls[0]["id"] == call_id
    assert calls[0]["caller_name"] == "Jamie"
    assert calls[0]["reason"] == "a billing question"


def test_receptionist_skips_followup_without_ai_consent(client, db_session):
    """Without AI-processing consent, qualify_caller() never even runs
    (model_version stays None) - asking a follow-up would be pointless
    since the follow-up's own re-qualification attempt would fail
    identically, so this must proceed straight to the post-capture menu."""
    token, account_id = _signup_and_login(client, "receptionistfollowup2@example.com")
    number = PhoneNumber(
        e164="+15550092222", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
        ai_receptionist_enabled=True,
    )
    db_session.add(number)
    db_session.commit()

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CArecfollow2", "To": "+15550092222", "From": "+15559992222",
        "SpeechResult": "Hi, calling about an order.",
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "could you tell me your name" not in response.text
    assert "press 2" in response.text.lower()


def test_receptionist_menu_press_one_closes_out_the_call(client, db_session):
    """Regression: pressing 1 (or the default/no-consent path reaching the
    menu at all) must still close out with the same message callers got
    before this menu existed - never trips callback_requested."""
    token, account_id = _signup_and_login(client, "receptionistmenu1@example.com")
    _capture_a_call(client, db_session, account_id, e164="+15550093333", call_sid="CArecmenu1")
    call_id = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()[0]["id"]

    menu_path = f"/media/receptionist/menu-select?receptionist_call_id={call_id}"
    menu_url = f"http://testserver{menu_path}"
    menu_params = {"CallSid": "CArecmenu1", "Digits": "1"}
    menu_signature = _twilio_signature(menu_url, menu_params)
    menu_response = client.post(menu_path, data=menu_params, headers={"X-Twilio-Signature": menu_signature})
    assert menu_response.status_code == 200
    assert "noted your message" in menu_response.text

    calls = client.get("/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert calls[0]["callback_requested"] is False


def test_receptionist_menu_press_two_then_window_records_callback_request(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "receptionistcallback1@example.com")
    number = PhoneNumber(
        e164="+15550094444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
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
                "name": "Riley", "company": None, "reason": "asked about a return",
                "summary": "Riley called asking about a return.", "urgency": "low",
                "callback_preference": None,
            },
            "groq/test",
        ),
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CAreccallback1", "To": "+15550094444", "From": "+15559994444",
        "SpeechResult": "Hi, I'd like to return an item.",
    }
    signature = _twilio_signature(url, params)
    respond_response = client.post(
        "/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature}
    )
    assert "press 2" in respond_response.text.lower()

    call_id = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()[0]["id"]

    menu_path = f"/media/receptionist/menu-select?receptionist_call_id={call_id}"
    menu_url = f"http://testserver{menu_path}"
    menu_params = {"CallSid": "CAreccallback1", "Digits": "2"}
    menu_signature = _twilio_signature(menu_url, menu_params)
    menu_response = client.post(menu_path, data=menu_params, headers={"X-Twilio-Signature": menu_signature})
    assert menu_response.status_code == 200
    assert "later today" in menu_response.text.lower()
    assert "/media/receptionist/callback-select" in menu_response.text

    callback_path = f"/media/receptionist/callback-select?receptionist_call_id={call_id}"
    callback_url = f"http://testserver{callback_path}"
    callback_params = {"CallSid": "CAreccallback1", "Digits": "2"}
    callback_signature = _twilio_signature(callback_url, callback_params)
    callback_response = client.post(
        callback_path, data=callback_params, headers={"X-Twilio-Signature": callback_signature},
    )
    assert callback_response.status_code == 200
    assert "later today" in callback_response.text.lower()

    calls = client.get("/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(calls) == 1
    assert calls[0]["id"] == call_id
    assert calls[0]["callback_requested"] is True
    assert calls[0]["callback_window"] == "today"


def test_receptionist_callback_select_timeout_still_records_the_request(client, db_session):
    """The caller already pressed 2 to reach this menu - a timeout/garbled
    digit on the window sub-menu should still record callback_requested,
    just with no specific window, rather than silently dropping the ask."""
    token, account_id = _signup_and_login(client, "receptionistcallback2@example.com")
    _capture_a_call(client, db_session, account_id, e164="+15550095555", call_sid="CAreccallback2")
    call_id = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()[0]["id"]

    callback_path = f"/media/receptionist/callback-select?receptionist_call_id={call_id}"
    callback_url = f"http://testserver{callback_path}"
    callback_params = {"CallSid": "CAreccallback2"}  # no Digits - timeout
    callback_signature = _twilio_signature(callback_url, callback_params)
    callback_response = client.post(
        callback_path, data=callback_params, headers={"X-Twilio-Signature": callback_signature},
    )
    assert callback_response.status_code == 200

    calls = client.get("/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert calls[0]["callback_requested"] is True
    assert calls[0]["callback_window"] is None


def test_receptionist_menu_timeout_closes_out_like_pressing_one(client, db_session):
    """build_dtmf_menu_response falls through to its own action_url with no
    Digits param on a timeout - menu-select must treat that the same as
    pressing 1, not error or hang."""
    token, account_id = _signup_and_login(client, "receptionistmenu2@example.com")
    _capture_a_call(client, db_session, account_id, e164="+15550096666", call_sid="CArecmenu2")
    call_id = client.get(
        "/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}
    ).json()[0]["id"]

    menu_path = f"/media/receptionist/menu-select?receptionist_call_id={call_id}"
    menu_url = f"http://testserver{menu_path}"
    menu_params = {"CallSid": "CArecmenu2"}  # no Digits - timeout
    menu_signature = _twilio_signature(menu_url, menu_params)
    menu_response = client.post(menu_path, data=menu_params, headers={"X-Twilio-Signature": menu_signature})
    assert menu_response.status_code == 200
    assert "noted your message" in menu_response.text


def test_receptionist_callback_preference_persisted_without_using_callback_menu(client, db_session, monkeypatch):
    """Groq's qualification extraction already produces callback_preference
    (a phone number or 'email' mentioned in speech) independent of whether
    the caller ever uses the DTMF callback menu - it must be persisted
    either way, not discarded."""
    token, account_id = _signup_and_login(client, "receptionistcbpref1@example.com")
    number = PhoneNumber(
        e164="+15550097777", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
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
                "name": "Morgan", "company": None, "reason": "asked about an invoice",
                "summary": "Morgan called about an invoice and prefers email.", "urgency": "low",
                "callback_preference": "email",
            },
            "groq/test",
        ),
    )

    url = "http://testserver/media/receptionist/respond"
    params = {
        "CallSid": "CAreccbpref1", "To": "+15550097777", "From": "+15559997777",
        "SpeechResult": "Hi, I have a question about my invoice, please email me.",
    }
    signature = _twilio_signature(url, params)
    client.post("/media/receptionist/respond", data=params, headers={"X-Twilio-Signature": signature})

    calls = client.get("/media/receptionist/calls", headers={"Authorization": f"Bearer {token}"}).json()
    assert len(calls) == 1
    assert calls[0]["callback_preference"] == "email"
    assert calls[0]["callback_requested"] is False
