"""ZL-COM-ENT-001 v3.0 - routing.transfer (Business+, blind/cold transfer
only). See app.media.service.transfer_call's docstring."""

from app.media.models import CallDirection, CallRecord


def _signup_and_login(client, email: str) -> tuple[str, str]:
    client.post(
        "/auth/signup",
        json={"account_name": "Transfer Test Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    return token, account_id


def _upgrade(db_session, account_id: str, plan_code: str):
    from app.billing import service as billing_service

    billing_service.change_plan(db_session, account_id, plan_code, actor="test-setup")


def _seed_in_progress_call(db_session, account_id: str, *, call_sid: str) -> CallRecord:
    call = CallRecord(
        account_id=account_id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid=call_sid,
        status="in-progress", duration=None,
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_transfer_requires_auth(client):
    response = client.post("/media/voice/calls/CAtransfer0000000000000000000/transfer", json={"destination": "+15551234567"})
    assert response.status_code == 401


def test_starter_plan_account_cannot_transfer_a_call(client, db_session):
    token, account_id = _signup_and_login(client, "transferstarter@example.com")
    _upgrade(db_session, account_id, "starter")
    call = _seed_in_progress_call(db_session, account_id, call_sid="CAtransferstarter1")

    response = client.post(
        f"/media/voice/calls/{call.provider_call_sid}/transfer",
        json={"destination": "+15551234567"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 402, response.text
    body = response.json()["detail"]
    assert body["code"] == "ENTITLEMENT_REQUIRED"
    assert body["entitlement"] == "routing.transfer"


def test_business_plan_account_can_transfer_an_in_progress_call(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "transferbusiness@example.com")
    _upgrade(db_session, account_id, "business")
    call = _seed_in_progress_call(db_session, account_id, call_sid="CAtransferbusiness1")

    redirect_calls = []
    monkeypatch.setattr(
        "app.media.service.telecom.redirect_call",
        lambda call_sid, twiml: redirect_calls.append((call_sid, twiml)) or {"sid": call_sid, "status": "in-progress"},
    )

    response = client.post(
        f"/media/voice/calls/{call.provider_call_sid}/transfer",
        json={"destination": "+15551234567"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sid"] == call.provider_call_sid
    assert len(redirect_calls) == 1
    assert redirect_calls[0][0] == call.provider_call_sid
    assert "+15551234567" in redirect_calls[0][1]


def test_transfer_rejects_a_call_that_is_not_in_progress(client, db_session):
    token, account_id = _signup_and_login(client, "transfernotinprogress@example.com")
    _upgrade(db_session, account_id, "business")
    call = CallRecord(
        account_id=account_id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid="CAtransfercompleted1",
        status="completed", duration=42,
    )
    db_session.add(call)
    db_session.commit()

    response = client.post(
        f"/media/voice/calls/{call.provider_call_sid}/transfer",
        json={"destination": "+15551234567"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409, response.text


def test_transfer_rejects_a_call_owned_by_another_account(client, db_session):
    token, account_id = _signup_and_login(client, "transferotheraccount@example.com")
    # Upgraded same as every other test in this file - the router-wide
    # trial-write gate (require_paid_or_read_only) would otherwise 402 this
    # request before it ever reaches the ownership check under test here.
    _upgrade(db_session, account_id, "business")
    _, other_account_id = _signup_and_login(client, "transferothervictim@example.com")
    _upgrade(db_session, other_account_id, "business")
    call = _seed_in_progress_call(db_session, other_account_id, call_sid="CAtransferother1")

    response = client.post(
        f"/media/voice/calls/{call.provider_call_sid}/transfer",
        json={"destination": "+15551234567"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403, response.text
