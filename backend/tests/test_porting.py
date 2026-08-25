from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Porting Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    token = login.json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (creating a
    # porting request) for a TRIALING account, and this file's tests are
    # about porting-workflow mechanics, not trial-gating, so upgrade to a
    # real paid plan here rather than adding this to every individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token, account_id


def _create_and_login_staff(db_session, client, email: str, role=PlatformStaffRole.SUPPORT) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _request_payload(phone_number: str = "+442079460100") -> dict:
    return {
        "phone_number": phone_number,
        "country": "GB",
        "current_carrier": "Old Carrier Ltd",
        "carrier_account_number": "OC-12345",
        "billing_name": "Porting Test Co",
        "billing_address": "1 Old Street, London",
    }


def test_create_request_requires_admin_role(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token, account_id = _signup_and_login(client, "portingmember1@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "portingmember1mate@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "portingmember1mate@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        "/porting/requests", json=_request_payload(),
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_create_and_list_my_requests(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, account_id = _signup_and_login(client, "portingowner1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/porting/requests", json=_request_payload(), headers=headers)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "submitted"
    assert body["phone_number"] == "+442079460100"
    assert body["account_id"] == account_id

    my_requests = client.get("/porting/requests/me", headers=headers).json()
    assert len(my_requests) == 1
    assert my_requests[0]["id"] == body["id"]


def test_create_request_rejects_a_number_already_on_platform(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, account_id = _signup_and_login(client, "portingowner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    number = PhoneNumber(
        e164="+442079460200", country="GB", status=PhoneNumberStatus.ACTIVE, account_id=account_id,
    )
    db_session.add(number)
    db_session.commit()

    response = client.post(
        "/porting/requests", json=_request_payload(phone_number="+442079460200"), headers=headers
    )
    assert response.status_code == 409
    assert "already a number" in response.json()["detail"].lower()


def test_create_request_rejects_duplicate_in_flight_request(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, account_id = _signup_and_login(client, "portingowner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    payload = _request_payload(phone_number="+442079460300")
    first = client.post("/porting/requests", json=payload, headers=headers)
    assert first.status_code == 201

    second = client.post("/porting/requests", json=payload, headers=headers)
    assert second.status_code == 409
    assert "already in progress" in second.json()["detail"].lower()


def test_cancel_request_by_owner(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, account_id = _signup_and_login(client, "portingowner4@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    request_id = client.post(
        "/porting/requests", json=_request_payload(phone_number="+442079460400"), headers=headers
    ).json()["id"]

    response = client.post(f"/porting/requests/{request_id}/cancel", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "canceled"

    second_cancel = client.post(f"/porting/requests/{request_id}/cancel", headers=headers)
    assert second_cancel.status_code == 409


def test_cancel_request_rejects_other_account(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token, account_id = _signup_and_login(client, "portingowner5@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    request_id = client.post(
        "/porting/requests", json=_request_payload(phone_number="+442079460500"), headers=owner_headers
    ).json()["id"]

    intruder_token, _ = _signup_and_login(client, "portingintruder5@example.com")
    response = client.post(
        f"/porting/requests/{request_id}/cancel", headers={"Authorization": f"Bearer {intruder_token}"}
    )
    assert response.status_code == 403


def test_list_all_requests_requires_staff_auth(client):
    assert client.get("/porting/requests").status_code == 401


def test_only_support_or_super_admin_staff_can_approve(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token, account_id = _signup_and_login(client, "portingowner6@example.com")
    request_id = client.post(
        "/porting/requests", json=_request_payload(phone_number="+442079460600"),
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()["id"]

    compliance_token = _create_and_login_staff(
        db_session, client, "portingcompliance6@zoikolocal.com", role=PlatformStaffRole.COMPLIANCE_OFFICER
    )
    response = client.post(
        f"/porting/requests/{request_id}/approve", headers={"Authorization": f"Bearer {compliance_token}"}
    )
    assert response.status_code == 403


def test_approve_reject_complete_workflow(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token, account_id = _signup_and_login(client, "portingowner7@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    request_id = client.post(
        "/porting/requests", json=_request_payload(phone_number="+442079460700"), headers=owner_headers
    ).json()["id"]

    staff_token = _create_and_login_staff(db_session, client, "portingsupport7@zoikolocal.com")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    # Can't complete before approval
    premature = client.post(
        f"/porting/requests/{request_id}/complete",
        json={"twilio_incoming_number_sid": "PNfake0000000000000000000000000"},
        headers=staff_headers,
    )
    assert premature.status_code == 409

    approve_response = client.post(f"/porting/requests/{request_id}/approve", headers=staff_headers)
    assert approve_response.status_code == 200
    assert approve_response.json()["status"] == "approved"

    # Approving twice is not a valid transition
    assert client.post(f"/porting/requests/{request_id}/approve", headers=staff_headers).status_code == 409

    complete_response = client.post(
        f"/porting/requests/{request_id}/complete",
        json={"twilio_incoming_number_sid": "PNfake0000000000000000000000000"},
        headers=staff_headers,
    )
    assert complete_response.status_code == 200, complete_response.text
    body = complete_response.json()
    assert body["status"] == "completed"
    assert body["created_number_id"]
    assert body["twilio_incoming_number_sid"] == "PNfake0000000000000000000000000"

    numbers = client.get("/numbers", headers=owner_headers).json()
    assert any(n["e164"] == "+442079460700" and n["status"] == "active" for n in numbers)


def test_reject_workflow_records_reason(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    owner_token, account_id = _signup_and_login(client, "portingowner8@example.com")
    request_id = client.post(
        "/porting/requests", json=_request_payload(phone_number="+442079460800"),
        headers={"Authorization": f"Bearer {owner_token}"},
    ).json()["id"]

    staff_token = _create_and_login_staff(db_session, client, "portingsupport8@zoikolocal.com")
    response = client.post(
        f"/porting/requests/{request_id}/reject",
        json={"reason": "Carrier account number could not be verified"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["rejection_reason"] == "Carrier account number could not be verified"

    # Staff list reflects it, scoped by status
    staff_list = client.get(
        "/porting/requests", params={"status": "rejected"}, headers={"Authorization": f"Bearer {staff_token}"}
    ).json()
    assert any(r["id"] == request_id for r in staff_list)
