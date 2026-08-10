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
        assert response.status_code == 204

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
