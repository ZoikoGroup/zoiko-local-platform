from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Ring Group Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (setting a ring
    # group) for a TRIALING account, and this file's tests are about ring-
    # group mechanics, not trial-gating, so upgrade to a real paid plan
    # here rather than adding this to every individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _make_active_number(client, db_session, token, e164: str) -> str:
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    return account_id


def _upgrade_to_business(db_session, account_id: str) -> None:
    """routing.shared_handling (a 2+-destination ring group) is Business+
    (ZL-COM-ENT-001) - a fresh signup's default free_trial plan grants no
    shared-handling capability."""
    from app.billing import service as billing_service

    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")


def test_owner_can_set_and_read_a_ring_group(client, db_session):
    token = _signup_and_login(client, "ring-owner1@example.com")
    account_id = _make_active_number(client, db_session, token, "+15550001111")
    _upgrade_to_business(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/numbers/+15550001111/ring-group",
        json={"destinations": ["+15552220001", "+15552220002"]},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert [d["destination_number"] for d in body] == ["+15552220001", "+15552220002"]
    assert [d["ring_order"] for d in body] == [0, 1]

    listed = client.get("/numbers/+15550001111/ring-group", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 2


def test_setting_a_ring_group_replaces_the_previous_one(client, db_session):
    token = _signup_and_login(client, "ring-owner2@example.com")
    account_id = _make_active_number(client, db_session, token, "+15550002222")
    _upgrade_to_business(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    client.put("/numbers/+15550002222/ring-group", json={"destinations": ["+15553330001"]}, headers=headers)
    response = client.put(
        "/numbers/+15550002222/ring-group", json={"destinations": ["+15553330002", "+15553330003"]},
        headers=headers,
    )
    assert [d["destination_number"] for d in response.json()] == ["+15553330002", "+15553330003"]


def test_empty_destinations_clears_the_ring_group(client, db_session):
    token = _signup_and_login(client, "ring-owner3@example.com")
    _make_active_number(client, db_session, token, "+15550003333")
    headers = {"Authorization": f"Bearer {token}"}

    client.put("/numbers/+15550003333/ring-group", json={"destinations": ["+15554440001"]}, headers=headers)
    response = client.put("/numbers/+15550003333/ring-group", json={"destinations": []}, headers=headers)
    assert response.json() == []


def test_free_trial_account_cannot_set_a_multi_destination_ring_group(client, db_session):
    """ZL-COM-ENT-001 §7 matrix: shared call handling (2+ destinations
    ringing at once) is Business+ only - a single destination is just
    personal forwarding and stays available to every plan."""
    token = _signup_and_login(client, "ring-freetrial1@example.com")
    _make_active_number(client, db_session, token, "+15550009991")
    headers = {"Authorization": f"Bearer {token}"}

    single = client.put(
        "/numbers/+15550009991/ring-group", json={"destinations": ["+15552220001"]}, headers=headers,
    )
    assert single.status_code == 200, single.text

    denied = client.put(
        "/numbers/+15550009991/ring-group",
        json={"destinations": ["+15552220001", "+15552220002"]},
        headers=headers,
    )
    assert denied.status_code == 402, denied.text
    body = denied.json()["detail"]
    assert body["code"] == "ENTITLEMENT_REQUIRED"
    assert body["entitlement"] == "routing.shared_handling"
    assert body["current_plan"] == "free_trial"


def test_ring_group_size_is_capped(client, db_session):
    token = _signup_and_login(client, "ring-owner4@example.com")
    _make_active_number(client, db_session, token, "+15550004444")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/numbers/+15550004444/ring-group",
        json={"destinations": [f"+1555000{i}" for i in range(6)]},
        headers=headers,
    )
    assert response.status_code == 422


def test_cannot_read_another_accounts_ring_group(client, db_session):
    token_a = _signup_and_login(client, "ring-owner5a@example.com")
    _make_active_number(client, db_session, token_a, "+15550005555")
    token_b = _signup_and_login(client, "ring-owner5b@example.com")

    response = client.get(
        "/numbers/+15550005555/ring-group", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert response.status_code == 404


def test_incoming_call_rings_all_configured_destinations_simultaneously(client, db_session):
    token = _signup_and_login(client, "ring-incoming1@example.com")
    account_id = _make_active_number(client, db_session, token, "+15550006666")
    _upgrade_to_business(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    client.put(
        "/numbers/+15550006666/routing", json={"forwarding_number": "+15559998888"}, headers=headers,
    )
    client.put(
        "/numbers/+15550006666/ring-group",
        json={"destinations": ["+15557770001", "+15557770002"]},
        headers=headers,
    )

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550006666", "From": "+15559990000", "CallSid": "CAring1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "+15557770001" in response.text
    assert "+15557770002" in response.text
    # The plain forwarding_number is NOT used once a ring group is configured.
    assert "+15559998888" not in response.text
    # Not a bare "<Number>" - build_ring_group_response puts
    # statusCallback/statusCallbackEvent as attributes on this noun.
    assert response.text.count("<Number ") == 2


def test_forward_fallback_routes_to_voicemail_on_no_answer(client, db_session):
    token = _signup_and_login(client, "ring-fallback1@example.com")
    _make_active_number(client, db_session, token, "+15550007777")

    url = "http://testserver/media/voice/forward-fallback"
    params = {"CallSid": "CAfallback1", "DialCallStatus": "no-answer"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/forward-fallback", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Record" in response.text


def test_forward_fallback_does_nothing_further_when_call_completed(client, db_session):
    token = _signup_and_login(client, "ring-fallback2@example.com")
    _make_active_number(client, db_session, token, "+15550008888")

    url = "http://testserver/media/voice/forward-fallback"
    params = {"CallSid": "CAfallback2", "DialCallStatus": "completed"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/forward-fallback", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Record" not in response.text
    assert "<Dial" not in response.text
