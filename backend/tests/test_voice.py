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
    token = response.json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (calls, routing
    # config) for a TRIALING account, and most of this file's tests are
    # about call-routing mechanics, not trial-gating, so upgrade to a real
    # paid plan here rather than adding this to every individual test.
    # (This also keeps test_enabling_ai_receptionist_is_blocked_without_
    # plan_or_addon_entitlement meaningful: on starter-without-the-addon,
    # its PUT .../routing call now reaches the real addon-entitlement
    # check instead of being pre-empted by this trial gate.)
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


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
    # A bare 204 reaches Twilio with an empty Content-Type header (its
    # error 12300) - the route returns a real empty TwiML document instead
    # (confirmed live via a real call's own Notifications log).
    assert status_response.status_code == 200

    calls_response = client.get("/media/voice/calls", headers={"Authorization": f"Bearer {token}"})
    assert calls_response.status_code == 200
    calls = calls_response.json()
    assert len(calls) == 1
    assert calls[0]["sid"] == "CAxyz123"
    assert calls[0]["status"] == "completed"
    assert calls[0]["duration"] == 42


def test_incoming_call_to_unrecognized_number_does_not_crash(client):
    # A call to a number we don't own (never purchased, or since released)
    # has no owning account - record_call's audit log_event call must not
    # require a real account_id here. Regression test for a real production
    # bug: log_event(actor_id=None, ...) raised ValueError because neither
    # actor nor actor_id was set, 500ing every inbound call to an
    # unrecognized number.
    url = "http://testserver/media/voice/incoming"
    params = {
        "To": "+15550000000", "From": "+15551234567", "CallSid": "CAunrecognized1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/incoming", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "isn't recognized" in response.text


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


def test_incoming_call_declines_voicemail_when_not_entitled(client, db_session):
    """ZL-COM-ENT-001 v3.0 - voicemail.enabled is seeded True on every real
    plan, so this is defense-in-depth for an account with no seeded rows
    at all (free_trial), not an expected path in practice. A number
    normally can't be owned by a free_trial account (purchase requires a
    paid plan) - inserted directly here to exercise the denial branch in
    isolation regardless of how such a number came to exist."""
    # Deliberately not _signup_and_login - that helper upgrades to starter
    # (which grants voicemail.enabled) to keep every OTHER test in this
    # file about call-routing mechanics, not trial-gating; this test needs
    # the real, ungraded free_trial account specifically.
    client.post(
        "/auth/signup",
        json={
            "account_name": "Voicemail Denied Co", "account_type": "business",
            "email": "voicemaildenied@example.com", "password": "supersecret123",
        },
    )
    token = client.post(
        "/auth/login", json={"email": "voicemaildenied@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    from app.billing import service as billing_service

    sub = billing_service.get_or_create_subscription(db_session, account_id)
    assert sub.plan_code == "free_trial"
    number = PhoneNumber(
        e164="+15550002222", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id
    )
    db_session.add(number)
    db_session.commit()

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550002222", "From": "+15559990000", "CallSid": "CAvmdenied1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Record" not in response.text
    assert "no one is available" in response.text


def test_starter_plan_account_cannot_set_business_hours(client, db_session):
    """ZL-COM-ENT-001 §7 matrix: business-hours routing is Business+ only -
    configuring hours at all (not just leaving them unset) is the gated
    capability. Uses the shared _signup_and_login (upgrades to starter)
    rather than a real free_trial account: app.core.deps.
    require_paid_or_read_only's router-wide gate blocks every write for a
    genuinely TRIALING account with a plain-string error (not the
    dict-shaped ENTITLEMENT_REQUIRED body this test checks), so the
    specific-entitlement path this test exercises only reaches on an
    already-paid plan that simply lacks routing.business_hours."""
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
    assert body["current_plan"] == "starter"


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


def test_browser_call_to_another_zoiko_number_routes_through_their_configured_handling(client, db_session):
    """ZL-COM-ENT-001 v3.0 - voice.app_to_app. A browser call to a number
    owned by a DIFFERENT Zoiko account must route through THAT account's
    own real call handling (here: their configured forwarding number),
    not a bare client-to-client bridge that would skip it entirely."""
    from app.media import service as media_service
    from app.numbering.numbers.models import CallerIdentity, CallerIdentityStatus
    from datetime import datetime, timezone

    caller_token = _signup_and_login(client, "apptoappcaller@example.com")
    caller_account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {caller_token}"}).json()["account_id"]
    caller_number = PhoneNumber(
        e164="+15550031111", country="US", status=PhoneNumberStatus.ACTIVE, account_id=caller_account_id,
    )
    db_session.add(caller_number)
    db_session.commit()
    db_session.add(CallerIdentity(
        phone_number_id=caller_number.id, account_id=caller_account_id, status=CallerIdentityStatus.VERIFIED,
        verification_source="test-fixture", verified_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    callee_token = _signup_and_login(client, "apptoappcallee@example.com")
    callee_account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {callee_token}"}).json()["account_id"]
    callee_number = PhoneNumber(
        e164="+15550032222", country="US", status=PhoneNumberStatus.ACTIVE, account_id=callee_account_id,
    )
    db_session.add(callee_number)
    db_session.commit()
    client.put(
        "/numbers/+15550032222/routing",
        json={"forwarding_number": "+15559998888"},
        headers={"Authorization": f"Bearer {callee_token}"},
    )

    result = media_service.handle_browser_connect(
        db_session, account_id=caller_account_id, from_number="+15550031111", to="+15550032222",
        call_sid="CAapptoapp1",
    )
    assert result["mode"] == "app_to_app"
    assert result["owner"].account_id == callee_account_id


def test_browser_call_falls_back_to_pstn_when_caller_lacks_app_to_app_entitlement(client, db_session):
    """A caller on a plan/state without voice.app_to_app (free_trial - no
    seeded rows at all) still reaches the destination normally, over
    PSTN - the entitlement gates an optimization, not the call itself."""
    from app.media import service as media_service
    from app.numbering.numbers.models import CallerIdentity, CallerIdentityStatus
    from datetime import datetime, timezone

    # Deliberately not upgraded off free_trial - the account this whole
    # test is about. A number normally can't be owned by a free_trial
    # account (purchase requires a paid plan) - inserted directly to
    # isolate testing this specific fallback branch.
    client.post(
        "/auth/signup",
        json={
            "account_name": "App To App Fallback Co", "account_type": "business",
            "email": "apptoappfallback@example.com", "password": "supersecret123",
        },
    )
    caller_token = client.post(
        "/auth/login", json={"email": "apptoappfallback@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    caller_account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {caller_token}"}).json()["account_id"]
    caller_number = PhoneNumber(
        e164="+15550033333", country="US", status=PhoneNumberStatus.ACTIVE, account_id=caller_account_id,
    )
    db_session.add(caller_number)
    db_session.commit()
    db_session.add(CallerIdentity(
        phone_number_id=caller_number.id, account_id=caller_account_id, status=CallerIdentityStatus.VERIFIED,
        verification_source="test-fixture", verified_at=datetime.now(timezone.utc),
    ))
    db_session.commit()

    callee_token = _signup_and_login(client, "apptoappfallbackcallee@example.com")
    callee_account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {callee_token}"}).json()["account_id"]
    callee_number = PhoneNumber(
        e164="+15550034444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=callee_account_id,
    )
    db_session.add(callee_number)
    db_session.commit()

    result = media_service.handle_browser_connect(
        db_session, account_id=caller_account_id, from_number="+15550033333", to="+15550034444",
        call_sid="CAapptoappfallback1",
    )
    assert result["mode"] == "pstn_bridge"
    assert result["destination"] == "+15550034444"
