from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.media.models import CallDirection, CallRecord
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


def test_forwarded_call_is_not_recorded_without_consent(client, db_session):
    """Architecture doc §2.2: 'Recording: off by default... must be
    consented.' A forwarding_number alone must not turn recording on."""
    token = _signup_and_login(client, "voicenorecord@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550001111", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    client.put(
        "/numbers/+15550001111/routing",
        json={"forwarding_number": "+15551112222"},
        headers={"Authorization": f"Bearer {token}"},
    )

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550001111", "From": "+15559990000", "CallSid": "CArecord0", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Dial" in response.text
    assert "record=" not in response.text
    assert "media/voice/recording-callback" not in response.text


def test_forwarded_call_twiml_requests_recording_with_consent(client, db_session):
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
    consent_response = client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert consent_response.status_code == 200

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
    from app.billing import service as billing_service

    token = _signup_and_login(client, "voicehours@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    # routing.business_hours is Business+ (ZL-COM-ENT-001) - a fresh signup's
    # default free_trial plan grants no business-hours capability.
    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")
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


def test_free_trial_account_cannot_set_business_hours(client, db_session):
    """ZL-COM-ENT-001 §7 matrix: business-hours routing is Business+ only -
    configuring hours at all (not just leaving them unset) is the gated
    capability, so a free_trial account must be denied with a real
    entitlement code."""
    token = _signup_and_login(client, "voicehoursdenied@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550009998", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    denied = client.put(
        "/numbers/+15550009998/routing",
        json={
            "forwarding_number": "+15551112222",
            "business_hours_start": "09:00:00",
            "business_hours_end": "17:00:00",
            "business_hours_timezone": "UTC",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 402, denied.text
    body = denied.json()["detail"]
    assert body["code"] == "ENTITLEMENT_REQUIRED"
    assert body["entitlement"] == "routing.business_hours"
    assert body["current_plan"] == "free_trial"


def _signup_and_login_with_account(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Get Call Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _seed_call_record(db_session, account_id: str, *, call_sid: str) -> CallRecord:
    call = CallRecord(
        account_id=account_id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid=call_sid,
        status="completed", duration=42,
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_get_call_requires_auth(client):
    response = client.get("/media/voice/calls/CAsomecallsid00000000000000000")
    assert response.status_code == 401


def test_get_call_rejects_other_account(client, db_session):
    """Security-review fix: this endpoint used to proxy straight to Twilio
    with no ownership check - any authenticated user could look up any
    other account's call metadata by SID."""
    _, owner_account_id = _signup_and_login_with_account(client, "getcallowner@example.com")
    call = _seed_call_record(db_session, owner_account_id, call_sid="CAidortest0000000000000000000001")

    intruder_token, _ = _signup_and_login_with_account(client, "getcallintruder@example.com")
    response = client.get(
        f"/media/voice/calls/{call.provider_call_sid}",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_get_call_rejects_a_call_sid_that_does_not_exist(client, db_session):
    token, _ = _signup_and_login_with_account(client, "getcallnoexist@example.com")
    response = client.get(
        "/media/voice/calls/CAdoesnotexist00000000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_routing_config_persists_dedicated_escalation_phone_number(client, db_session):
    from app.billing import service as billing_service

    token = _signup_and_login(client, "voiceescalationfield@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    # Real gap fix (ZL-COM-ENT-001): ai_receptionist.enabled is now Pro+
    # (or Starter/Business with the add-on) - see billing_service.
    # has_ai_receptionist_capability.
    billing_service.change_plan(db_session, account_id, "pro", actor="test-setup")
    number = PhoneNumber(
        e164="+15550001010", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    # AI Receptionist is plan/add-on gated (billing.service.
    # is_ai_receptionist_enabled_for_account) - a fresh signup's default
    # trial plan grants neither, so enable the add-on first.
    client.put(
        "/billing/subscription/ai-receptionist-addon", json={"enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    routing_response = client.put(
        "/numbers/+15550001010/routing",
        json={"ai_receptionist_enabled": True, "escalation_phone_number": "+15559998877"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert routing_response.status_code == 200
    assert routing_response.json()["escalation_phone_number"] == "+15559998877"

    get_response = client.get("/numbers", headers={"Authorization": f"Bearer {token}"})
    numbers = get_response.json()
    assert numbers[0]["escalation_phone_number"] == "+15559998877"


def test_enabling_ai_receptionist_is_blocked_without_plan_or_addon_entitlement(client, db_session):
    """A fresh signup's default trial plan grants no AI Receptionist
    minutes and has the add-on off - configure_routing must reject turning
    the per-number toggle on rather than silently allowing a free feature."""
    token = _signup_and_login(client, "voiceaidenied@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550004444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    denied = client.put(
        "/numbers/+15550004444/routing",
        json={"ai_receptionist_enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert denied.status_code == 402, denied.text
    assert denied.json()["code"] == "ADDON_REQUIRED"

    client.put(
        "/billing/subscription/ai-receptionist-addon", json={"enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    allowed = client.put(
        "/numbers/+15550004444/routing",
        json={"ai_receptionist_enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["ai_receptionist_enabled"] is True


def test_forward_fallback_goes_to_ai_receptionist_when_forwarding_is_missed(client, db_session):
    """Confirmed live (2026-08-22): a number with BOTH forwarding and AI
    Receptionist enabled used to get plain voicemail on a missed forwarded
    call - AI never got a chance to catch what the human missed. This
    proves forward-fallback now offers the AI Receptionist greeting
    instead of going straight to voicemail."""
    from app.billing import service as billing_service

    token = _signup_and_login(client, "voicefallbackai@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    billing_service.change_plan(db_session, account_id, "pro", actor="test-setup")
    number = PhoneNumber(
        e164="+15550002222", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    # AI Receptionist is plan/add-on gated (billing.service.
    # is_ai_receptionist_enabled_for_account) - a fresh signup's default
    # trial plan grants neither, so enable the add-on first.
    client.put(
        "/billing/subscription/ai-receptionist-addon", json={"enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    client.put(
        "/numbers/+15550002222/routing",
        json={"forwarding_number": "+15551112222", "ai_receptionist_enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    fallback_url = "http://testserver/media/voice/forward-fallback"
    fallback_params = {
        "To": "+15550002222", "From": "+15559990000", "CallSid": "CAfallback1", "DialCallStatus": "no-answer",
    }
    signature = _twilio_signature(fallback_url, fallback_params)
    response = client.post(
        "/media/voice/forward-fallback", data=fallback_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Gather" in response.text
    assert "media/receptionist/respond" in response.text
    assert "<Record" not in response.text


def test_forward_fallback_goes_to_voicemail_without_ai_receptionist(client, db_session):
    """A number with forwarding but AI Receptionist off keeps the original
    overflow-to-voicemail behavior - this is a regression guard, not a new
    requirement."""
    token = _signup_and_login(client, "voicefallbackvm@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(
        e164="+15550003333", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    client.put(
        "/numbers/+15550003333/routing",
        json={"forwarding_number": "+15551112222"},
        headers={"Authorization": f"Bearer {token}"},
    )

    fallback_url = "http://testserver/media/voice/forward-fallback"
    fallback_params = {
        "To": "+15550003333", "From": "+15559990000", "CallSid": "CAfallback2", "DialCallStatus": "no-answer",
    }
    signature = _twilio_signature(fallback_url, fallback_params)
    response = client.post(
        "/media/voice/forward-fallback", data=fallback_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Record" in response.text
    assert "media/receptionist/respond" not in response.text


def test_forward_fallback_does_nothing_when_call_was_answered(client, db_session):
    from app.billing import service as billing_service

    token = _signup_and_login(client, "voicefallbackok@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    billing_service.change_plan(db_session, account_id, "pro", actor="test-setup")
    number = PhoneNumber(
        e164="+15550004444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    client.put(
        "/numbers/+15550004444/routing",
        json={"forwarding_number": "+15551112222", "ai_receptionist_enabled": True},
        headers={"Authorization": f"Bearer {token}"},
    )

    fallback_url = "http://testserver/media/voice/forward-fallback"
    fallback_params = {
        "To": "+15550004444", "From": "+15559990000", "CallSid": "CAfallback3", "DialCallStatus": "completed",
    }
    signature = _twilio_signature(fallback_url, fallback_params)
    response = client.post(
        "/media/voice/forward-fallback", data=fallback_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Gather" not in response.text
    assert "<Record" not in response.text


def test_get_call_succeeds_for_the_owning_account(client, db_session, monkeypatch):
    token, account_id = _signup_and_login_with_account(client, "getcallowner2@example.com")
    call = _seed_call_record(db_session, account_id, call_sid="CAidortest0000000000000000000002")

    monkeypatch.setattr(
        "app.media.voice.telecom.get_call",
        lambda call_sid: {"sid": call_sid, "status": "completed", "to": "+15550001111", "from": "+15559990000", "duration": 42},
    )

    response = client.get(
        f"/media/voice/calls/{call.provider_call_sid}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["sid"] == call.provider_call_sid
