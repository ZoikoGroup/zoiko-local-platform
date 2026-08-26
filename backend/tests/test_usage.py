from datetime import datetime, timezone

from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import CallerIdentity, CallerIdentityStatus, PhoneNumber, PhoneNumberStatus


def _verify_caller_id(db_session, number: PhoneNumber) -> None:
    """Real purchases auto-create a VERIFIED CallerIdentity (see
    assert_caller_id_authorized) - tests that build a PhoneNumber directly
    instead of going through purchase_number must create one too, or
    outbound calls get rejected as an unauthorized caller ID."""
    db_session.add(CallerIdentity(
        phone_number_id=number.id, account_id=number.account_id, status=CallerIdentityStatus.VERIFIED,
        verification_source="test-fixture", verified_at=datetime.now(timezone.utc),
    ))
    db_session.commit()


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
    token = response.json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (placing a call,
    # opening/resolving a usage dispute) for a TRIALING account, and this
    # file's tests are about usage-metering mechanics, not trial-gating,
    # so upgrade to a real paid plan here rather than adding this to every
    # individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _place_outbound_call(client, db_session, monkeypatch, token, account_id, e164, to, call_sid):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": call_sid, "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    number = PhoneNumber(e164=e164, country="GB", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    _verify_caller_id(db_session, number)

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
    # /status-callback returns a real empty TwiML doc (200), not a bare 204 -
    # a bare 204 reaches Twilio with an empty Content-Type header (its error
    # 12300), confirmed live via a real call's own Notifications log.
    assert callback_response.status_code == 200

    usage_response = client.get("/usage", headers={"Authorization": f"Bearer {token}"})
    assert usage_response.status_code == 200
    events = usage_response.json()
    assert len(events) == 1
    assert events[0]["event_type"] == "call_seconds"
    assert events[0]["quantity"] == 125
    assert events[0]["unit"] == "seconds"
    assert events[0]["country_band"] == "GB"
    # GB is seeded at 2 cents/minute; 125 seconds rounds up to 3 minutes.
    assert events[0]["estimated_cost_cents"] == 6


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
        assert response.status_code == 200

    usage_response = client.get("/usage", headers={"Authorization": f"Bearer {token}"})
    assert len(usage_response.json()) == 1


def test_completed_call_falls_back_to_the_default_rate_for_an_unseeded_country(client, db_session, monkeypatch):
    """The calling number's own country only has a rate row for the 8
    curated countries - anything else (a number bought before this country
    ended up curated, or a staff-added rate that hasn't been set) must
    still price using the DEFAULT_RATE_COUNTRY fallback rather than leaving
    estimated_cost_cents null."""
    token = _signup_and_login(client, "usagefallback@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAusagefallback1", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    number = PhoneNumber(e164="+81312345678", country="JP", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    _verify_caller_id(db_session, number)
    response = client.post(
        "/media/voice/outbound", json={"to": "+15559990002", "from": "+81312345678"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text

    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAusagefallback1", "CallStatus": "completed", "CallDuration": "60"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})

    events = client.get("/usage", headers={"Authorization": f"Bearer {token}"}).json()
    assert events[0]["country_band"] == "JP"
    # XX (default fallback) is seeded at 5 cents/minute.
    assert events[0]["estimated_cost_cents"] == 5


def test_customer_can_view_the_calling_rate_card(client):
    token = _signup_and_login(client, "ratecard1@example.com")
    response = client.get("/usage/calling-rates", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    rates = {r["country"]: r["price_per_minute_cents"] for r in response.json()}
    assert rates["US"] == 1
    assert rates["XX"] == 5


def test_staff_can_update_a_calling_rate(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="staffrate1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffrate1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    response = client.put(
        "/staff/calling-rates", json={"country": "US", "price_per_minute_cents": 9, "currency": "USD"},
        headers=staff_headers,
    )
    assert response.status_code == 200
    assert response.json()["price_per_minute_cents"] == 9

    token = _signup_and_login(client, "ratecard2@example.com")
    rates = {
        r["country"]: r["price_per_minute_cents"]
        for r in client.get("/usage/calling-rates", headers={"Authorization": f"Bearer {token}"}).json()
    }
    assert rates["US"] == 9


def test_non_super_admin_staff_cannot_update_calling_rates(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="staffrate2@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffrate2@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.put(
        "/staff/calling-rates", json={"country": "US", "price_per_minute_cents": 9},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403


def test_customer_can_view_the_number_and_ai_usage_rate_cards(client):
    """Global Plans, Pricing & Commercial Launch Standard doc §5.1/§5.3 -
    the real, doc-approved baseline figures seeded by migration 61bc6e50e6db."""
    token = _signup_and_login(client, "ratecard3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    number_rates = client.get("/usage/number-rates", headers=headers)
    assert number_rates.status_code == 200
    default_rate = next(r for r in number_rates.json() if r["country"] == "XX")
    assert default_rate["recurring_price_cents"] == 499
    assert default_rate["is_placeholder"] is False

    ai_rate = client.get("/usage/ai-usage-rate", headers=headers)
    assert ai_rate.status_code == 200
    assert ai_rate.json()["overage_price_cents_per_minute"] == 39
    assert ai_rate.json()["is_placeholder"] is False


def test_staff_can_update_number_and_ai_usage_rates(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="staffrate3@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffrate3@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    number_rate = client.put(
        "/staff/number-rates",
        json={"country": "GB", "number_type": "local", "recurring_price_cents": 599, "currency": "USD"},
        headers=staff_headers,
    )
    assert number_rate.status_code == 200
    assert number_rate.json()["recurring_price_cents"] == 599

    ai_rate = client.put(
        "/staff/ai-usage-rate", json={"overage_price_cents_per_minute": 45}, headers=staff_headers,
    )
    assert ai_rate.status_code == 200
    assert ai_rate.json()["overage_price_cents_per_minute"] == 45

    token = _signup_and_login(client, "ratecard4@example.com")
    ai_rate_view = client.get(
        "/usage/ai-usage-rate", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert ai_rate_view["overage_price_cents_per_minute"] == 45


def test_non_super_admin_staff_cannot_update_number_or_ai_usage_rates(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="staffrate4@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    staff_token = client.post(
        "/staff/login", json={"email": "staffrate4@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    assert client.put(
        "/staff/number-rates", json={"country": "GB", "recurring_price_cents": 599}, headers=staff_headers,
    ).status_code == 403
    assert client.put(
        "/staff/ai-usage-rate", json={"overage_price_cents_per_minute": 45}, headers=staff_headers,
    ).status_code == 403


# --- Usage disputes / adjustments (append-only billing correction trail) ---


def test_owner_can_open_a_dispute_on_their_own_usage_event(client, db_session, monkeypatch):
    token = _signup_and_login(client, "disputeopen1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550004444", "+15559990010", "CAdispute1")
    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAdispute1", "CallStatus": "completed", "CallDuration": "600"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})

    headers = {"Authorization": f"Bearer {token}"}
    usage_event_id = client.get("/usage", headers=headers).json()[0]["id"]

    response = client.post(
        "/usage/disputes", json={"usage_event_id": usage_event_id, "reason": "I was never on this call"},
        headers=headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["usage_event_id"] == usage_event_id

    own_list = client.get("/usage/disputes", headers=headers).json()
    assert len(own_list) == 1
    assert own_list[0]["id"] == body["id"]


def test_cannot_dispute_a_usage_event_belonging_to_another_account(client, db_session, monkeypatch):
    token1 = _signup_and_login(client, "disputeother1@example.com")
    account_id1 = client.get("/auth/me", headers={"Authorization": f"Bearer {token1}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token1, account_id1, "+15550005555", "+15559990011", "CAdispute2")
    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAdispute2", "CallStatus": "completed", "CallDuration": "60"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})
    usage_event_id = client.get("/usage", headers={"Authorization": f"Bearer {token1}"}).json()[0]["id"]

    token2 = _signup_and_login(client, "disputeother2@example.com")
    response = client.post(
        "/usage/disputes", json={"usage_event_id": usage_event_id, "reason": "not mine"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert response.status_code == 404


def test_staff_resolves_dispute_with_adjustment_and_updates_the_usage_event(client, db_session, monkeypatch):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    token = _signup_and_login(client, "disputeadjust1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550006666", "+15559990012", "CAdispute3")
    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAdispute3", "CallStatus": "completed", "CallDuration": "600"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})

    headers = {"Authorization": f"Bearer {token}"}
    original_event = client.get("/usage", headers=headers).json()[0]
    usage_event_id = original_event["id"]
    original_cost = original_event["estimated_cost_cents"]
    assert original_cost > 0

    dispute_id = client.post(
        "/usage/disputes", json={"usage_event_id": usage_event_id, "reason": "call dropped after 10 seconds"},
        headers=headers,
    ).json()["id"]

    staff_service.create_staff(
        db_session, email="disputestaff1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_token = client.post(
        "/staff/login", json={"email": "disputestaff1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    resolve_response = client.post(
        f"/usage/disputes/{dispute_id}/resolve",
        json={"status": "resolved_adjusted", "notes": "confirmed dropped call, adjusted to 10s pricing", "new_estimated_cost_cents": 1},
        headers=staff_headers,
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["status"] == "resolved_adjusted"

    updated_event = client.get("/usage", headers=headers).json()[0]
    assert updated_event["estimated_cost_cents"] == 1
    assert updated_event["estimated_cost_cents"] != original_cost

    from app.usage.models import UsageAdjustment
    adjustment = db_session.query(UsageAdjustment).filter(UsageAdjustment.usage_event_id == usage_event_id).first()
    assert adjustment is not None
    assert adjustment.previous_estimated_cost_cents == original_cost
    assert adjustment.new_estimated_cost_cents == 1
    assert adjustment.dispute_id == dispute_id


def test_staff_can_deny_a_dispute_without_touching_the_usage_event(client, db_session, monkeypatch):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    token = _signup_and_login(client, "disputedeny1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550007777", "+15559990013", "CAdispute4")
    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAdispute4", "CallStatus": "completed", "CallDuration": "60"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})

    headers = {"Authorization": f"Bearer {token}"}
    original_cost = client.get("/usage", headers=headers).json()[0]["estimated_cost_cents"]
    usage_event_id = client.get("/usage", headers=headers).json()[0]["id"]
    dispute_id = client.post(
        "/usage/disputes", json={"usage_event_id": usage_event_id, "reason": "disagree with the rate"},
        headers=headers,
    ).json()["id"]

    staff_service.create_staff(
        db_session, email="disputestaff2@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_token = client.post(
        "/staff/login", json={"email": "disputestaff2@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    resolve_response = client.post(
        f"/usage/disputes/{dispute_id}/resolve",
        json={"status": "resolved_denied", "notes": "rate card is correct"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["status"] == "resolved_denied"

    unchanged_cost = client.get("/usage", headers=headers).json()[0]["estimated_cost_cents"]
    assert unchanged_cost == original_cost


def test_non_super_admin_staff_cannot_resolve_a_dispute(client, db_session, monkeypatch):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    token = _signup_and_login(client, "disputenonadmin1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550008888", "+15559990014", "CAdispute5")
    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAdispute5", "CallStatus": "completed", "CallDuration": "60"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})

    headers = {"Authorization": f"Bearer {token}"}
    usage_event_id = client.get("/usage", headers=headers).json()[0]["id"]
    dispute_id = client.post(
        "/usage/disputes", json={"usage_event_id": usage_event_id, "reason": "test"}, headers=headers,
    ).json()["id"]

    staff_service.create_staff(
        db_session, email="disputestaff3@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    staff_token = client.post(
        "/staff/login", json={"email": "disputestaff3@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.post(
        f"/usage/disputes/{dispute_id}/resolve", json={"status": "resolved_denied"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403


def test_cannot_resolve_an_already_resolved_dispute(client, db_session, monkeypatch):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    token = _signup_and_login(client, "disputetwice1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _place_outbound_call(client, db_session, monkeypatch, token, account_id, "+15550009999", "+15559990015", "CAdispute6")
    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAdispute6", "CallStatus": "completed", "CallDuration": "60"}
    signature = _twilio_signature(callback_url, callback_params)
    client.post("/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature})

    headers = {"Authorization": f"Bearer {token}"}
    usage_event_id = client.get("/usage", headers=headers).json()[0]["id"]
    dispute_id = client.post(
        "/usage/disputes", json={"usage_event_id": usage_event_id, "reason": "test"}, headers=headers,
    ).json()["id"]

    staff_service.create_staff(
        db_session, email="disputestaff4@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_token = client.post(
        "/staff/login", json={"email": "disputestaff4@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    client.post(f"/usage/disputes/{dispute_id}/resolve", json={"status": "resolved_denied"}, headers=staff_headers)
    second_attempt = client.post(
        f"/usage/disputes/{dispute_id}/resolve", json={"status": "resolved_denied"}, headers=staff_headers,
    )
    assert second_attempt.status_code == 409


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
