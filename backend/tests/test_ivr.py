from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "IVR Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (setting an IVR
    # menu) for a TRIALING account, and this file's tests are about IVR
    # mechanics, not trial-gating, so upgrade to a real paid plan here
    # rather than adding this to every individual test.
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


def test_owner_can_set_and_read_an_ivr_menu(client, db_session):
    token = _signup_and_login(client, "ivr-owner1@example.com")
    _make_active_number(client, db_session, token, "+15550001111")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/numbers/+15550001111/ivr",
        json={"greeting": "Press 1 for sales, 2 for support.", "options": {"1": "+15552220001", "2": "+15552220002"}},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["greeting"] == "Press 1 for sales, 2 for support."
    assert {o["digit"]: o["destination_number"] for o in body["options"]} == {
        "1": "+15552220001", "2": "+15552220002",
    }

    fetched = client.get("/numbers/+15550001111/ivr", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["greeting"] == "Press 1 for sales, 2 for support."


def test_ivr_menu_rejects_an_invalid_digit(client, db_session):
    token = _signup_and_login(client, "ivr-owner2@example.com")
    _make_active_number(client, db_session, token, "+15550002222")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/numbers/+15550002222/ivr",
        json={"greeting": "Menu", "options": {"x": "+15552220001"}},
        headers=headers,
    )
    assert response.status_code == 422


def test_ivr_menu_rejects_an_empty_greeting(client, db_session):
    token = _signup_and_login(client, "ivr-owner3@example.com")
    _make_active_number(client, db_session, token, "+15550003333")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/numbers/+15550003333/ivr",
        json={"greeting": "   ", "options": {"1": "+15552220001"}},
        headers=headers,
    )
    assert response.status_code == 422


def test_clear_ivr_menu(client, db_session):
    token = _signup_and_login(client, "ivr-owner4@example.com")
    _make_active_number(client, db_session, token, "+15550004444")
    headers = {"Authorization": f"Bearer {token}"}

    client.put(
        "/numbers/+15550004444/ivr", json={"greeting": "Menu", "options": {"1": "+15552220001"}}, headers=headers,
    )
    delete_response = client.delete("/numbers/+15550004444/ivr", headers=headers)
    assert delete_response.status_code == 204

    fetched = client.get("/numbers/+15550004444/ivr", headers=headers).json()
    assert fetched["greeting"] is None
    assert fetched["options"] == []


def test_cannot_read_another_accounts_ivr_menu(client, db_session):
    token_a = _signup_and_login(client, "ivr-owner5a@example.com")
    _make_active_number(client, db_session, token_a, "+15550005555")
    token_b = _signup_and_login(client, "ivr-owner5b@example.com")

    response = client.get("/numbers/+15550005555/ivr", headers={"Authorization": f"Bearer {token_b}"})
    assert response.status_code == 404


def test_incoming_call_offers_the_ivr_menu_when_configured(client, db_session):
    token = _signup_and_login(client, "ivr-incoming1@example.com")
    _make_active_number(client, db_session, token, "+15550006666")
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        "/numbers/+15550006666/ivr",
        json={"greeting": "Press 1 for sales.", "options": {"1": "+15557770001"}},
        headers=headers,
    )

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550006666", "From": "+15559990000", "CallSid": "CAivr1", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Gather" in response.text
    assert "Press 1 for sales." in response.text


def test_incoming_call_skips_ivr_when_not_configured(client, db_session):
    token = _signup_and_login(client, "ivr-incoming2@example.com")
    _make_active_number(client, db_session, token, "+15550007777")

    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550007777", "From": "+15559990000", "CallSid": "CAivr2", "CallStatus": "ringing",
    }
    signature = _twilio_signature(incoming_url, incoming_params)
    response = client.post(
        "/media/voice/incoming", data=incoming_params, headers={"X-Twilio-Signature": signature}
    )
    assert response.status_code == 200
    assert "<Gather" not in response.text
    assert "<Record" in response.text  # falls straight to voicemail - no forwarding configured


def test_ivr_select_dials_the_matching_digits_destination(client, db_session):
    token = _signup_and_login(client, "ivr-select1@example.com")
    _make_active_number(client, db_session, token, "+15550008888")
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        "/numbers/+15550008888/ivr",
        json={"greeting": "Menu", "options": {"1": "+15557770001", "2": "+15557770002"}},
        headers=headers,
    )

    url = "http://testserver/media/voice/ivr-select"
    params = {"To": "+15550008888", "From": "+15559990000", "CallSid": "CAselect1", "Digits": "2"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/ivr-select", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "+15557770002" in response.text
    assert "+15557770001" not in response.text


def test_ivr_select_falls_through_to_default_on_unrecognized_digit(client, db_session):
    token = _signup_and_login(client, "ivr-select2@example.com")
    _make_active_number(client, db_session, token, "+15550009999")
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        "/numbers/+15550009999/ivr", json={"greeting": "Menu", "options": {"1": "+15557770001"}}, headers=headers,
    )

    url = "http://testserver/media/voice/ivr-select"
    params = {"To": "+15550009999", "From": "+15559990000", "CallSid": "CAselect2", "Digits": "9"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/ivr-select", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    # No matching option for "9" and no forwarding configured -> voicemail.
    assert "<Record" in response.text


def test_ivr_no_input_falls_through_to_default(client, db_session):
    token = _signup_and_login(client, "ivr-noinput1@example.com")
    _make_active_number(client, db_session, token, "+15550001010")
    headers = {"Authorization": f"Bearer {token}"}
    client.put(
        "/numbers/+15550001010/ivr", json={"greeting": "Menu", "options": {"1": "+15557770001"}}, headers=headers,
    )

    url = "http://testserver/media/voice/ivr-no-input"
    params = {"To": "+15550001010", "From": "+15559990000", "CallSid": "CAnoinput1"}
    signature = _twilio_signature(url, params)
    response = client.post("/media/voice/ivr-no-input", data=params, headers={"X-Twilio-Signature": signature})
    assert response.status_code == 200
    assert "<Record" in response.text
