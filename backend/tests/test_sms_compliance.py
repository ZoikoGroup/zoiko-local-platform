from app.compliance.models import ComplianceRule
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={"account_name": "SMS Compliance Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    account_id = signup.json()["account_id"]
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    return token, account_id


def _make_number(db_session, account_id: str, e164: str, country: str = "US", sms_enabled: bool = False) -> PhoneNumber:
    number = PhoneNumber(
        e164=e164, country=country, status=PhoneNumberStatus.ACTIVE, account_id=account_id, sms_enabled=sms_enabled
    )
    db_session.add(number)
    db_session.commit()
    db_session.refresh(number)
    return number


def _approve_via_staff(client, db_session, case_id: str, email: str) -> None:
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN)

    staff_token = client.post("/staff/login", json={"email": email, "password": "staffpass123"}).json()["access_token"]
    approve = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert approve.status_code == 200, approve.text


def test_enabling_sms_succeeds_when_no_compliance_rule_is_active_for_the_country(client, db_session):
    token, account_id = _signup_and_login(client, "smscompliance1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550040001")

    response = client.put(f"/numbers/{number.e164}/routing", json={"sms_enabled": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["sms_enabled"] is True


def test_enabling_sms_is_blocked_without_an_approved_compliance_case(client, db_session):
    db_session.add(
        ComplianceRule(country="US", requirement_type="sms_business_messaging", required_documents=["a2p_10dlc_brand"])
    )
    db_session.commit()

    token, account_id = _signup_and_login(client, "smscompliance2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550040002")

    response = client.put(f"/numbers/{number.e164}/routing", json={"sms_enabled": True}, headers=headers)
    assert response.status_code == 403
    assert "compliance case" in response.json()["detail"]

    db_session.refresh(number)
    assert number.sms_enabled is False


def test_enabling_sms_succeeds_once_compliance_case_is_approved(client, db_session):
    db_session.add(
        ComplianceRule(country="US", requirement_type="sms_business_messaging", required_documents=["a2p_10dlc_brand"])
    )
    db_session.commit()

    token, account_id = _signup_and_login(client, "smscompliance3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550040003")

    case_response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "US", "requirement_type": "sms_business_messaging"},
        headers=headers,
    )
    assert case_response.status_code == 201, case_response.text
    case_id = case_response.json()["id"]

    _approve_via_staff(client, db_session, case_id, "staffsmscompliance3@zoikolocal.com")

    response = client.put(f"/numbers/{number.e164}/routing", json={"sms_enabled": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["sms_enabled"] is True


def test_disabling_sms_never_requires_compliance(client, db_session):
    db_session.add(
        ComplianceRule(country="US", requirement_type="sms_business_messaging", required_documents=["a2p_10dlc_brand"])
    )
    db_session.commit()

    token, account_id = _signup_and_login(client, "smscompliance4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550040004", sms_enabled=True)

    response = client.put(f"/numbers/{number.e164}/routing", json={"sms_enabled": False}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["sms_enabled"] is False


def test_whatsapp_is_not_gated_by_the_sms_compliance_rule(client, db_session):
    db_session.add(
        ComplianceRule(country="US", requirement_type="sms_business_messaging", required_documents=["a2p_10dlc_brand"])
    )
    db_session.commit()

    token, account_id = _signup_and_login(client, "smscompliance5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    number = _make_number(db_session, account_id, "+15550040005")

    response = client.put(f"/numbers/{number.e164}/routing", json={"whatsapp_enabled": True}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["whatsapp_enabled"] is True
    assert response.json()["sms_enabled"] is False
