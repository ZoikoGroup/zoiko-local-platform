from app.numbering.numbers.models import NumberEligibilityRule


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Eligibility Test Co",
            "account_type": "individual",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    token = response.json()["access_token"]
    client.post(
        "/compliance/consent",
        json={"consent_type": "emergency_calling_acknowledged"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def _reserve(client, headers, e164: str, country: str = "US", number_type: str = "local"):
    response = client.post(
        "/numbers/reserve", json={"e164": e164, "country": country, "number_type": number_type}, headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _stub_buy_number(monkeypatch):
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164: {"sid": "PN_fake_sid", "phone_number": e164, "capabilities": {}},
    )


def _create_super_admin(client, db_session, email: str) -> str:
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN)
    return client.post("/staff/login", json={"email": email, "password": "staffpass123"}).json()["access_token"]


def test_purchase_succeeds_when_no_eligibility_rule_is_active(client, monkeypatch):
    """No NumberEligibilityRule row exists for 'US'/'local' - the gate must
    be a complete no-op, same regression-safety bar as the compliance gate's
    own "no rule configured" test."""
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "eligbuyer1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550091111")

    response = client.post("/numbers/purchase", json={"e164": "+15550091111"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_purchase_is_blocked_without_an_approved_eligibility_case(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    db_session.add(NumberEligibilityRule(country="DE", number_type="local", required_evidence=["business_address_proof"]))
    db_session.commit()

    token = _signup_and_login(client, "eligbuyer2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+4930555091112", country="DE")

    response = client.post("/numbers/purchase", json={"e164": "+4930555091112"}, headers=headers)
    assert response.status_code == 403
    assert "eligibility" in response.json()["detail"].lower()


def test_purchase_blocked_by_eligibility_persists_compliance_pending_status(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    db_session.add(NumberEligibilityRule(country="DE", number_type="local", required_evidence=["business_address_proof"]))
    db_session.commit()

    token = _signup_and_login(client, "eligbuyer3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+4930555091113", country="DE")
    client.post("/numbers/purchase", json={"e164": "+4930555091113"}, headers=headers)

    from app.numbering.numbers.models import PhoneNumber

    number = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == "+4930555091113").first()
    db_session.refresh(number)
    assert number.status.value == "compliance_pending"


def test_purchase_succeeds_once_eligibility_case_is_approved(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    db_session.add(NumberEligibilityRule(country="DE", number_type="local", required_evidence=["business_address_proof"]))
    db_session.commit()

    token = _signup_and_login(client, "eligbuyer4@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+4930555091114", country="DE")
    client.post("/numbers/purchase", json={"e164": "+4930555091114"}, headers=headers)

    cases = client.get("/numbers/eligibility-cases", headers=headers).json()
    assert len(cases) == 1
    case_id = cases[0]["id"]
    assert cases[0]["status"] == "pending"

    staff_token = _create_super_admin(client, db_session, "eligstaff4@zoikolocal.com")
    approve = client.post(
        f"/staff/number-eligibility-cases/{case_id}/approve",
        json={"notes": "verified business address"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert approve.status_code == 200, approve.text
    assert approve.json()["status"] == "approved"

    response = client.post("/numbers/purchase", json={"e164": "+4930555091114"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_rejected_eligibility_case_can_be_retried_with_fresh_evidence(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    db_session.add(NumberEligibilityRule(country="DE", number_type="local", required_evidence=["business_address_proof"]))
    db_session.commit()

    token = _signup_and_login(client, "eligbuyer5@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+4930555091115", country="DE")
    client.post("/numbers/purchase", json={"e164": "+4930555091115"}, headers=headers)
    case_id = client.get("/numbers/eligibility-cases", headers=headers).json()[0]["id"]

    staff_token = _create_super_admin(client, db_session, "eligstaff5@zoikolocal.com")
    reject = client.post(
        f"/staff/number-eligibility-cases/{case_id}/reject",
        json={"notes": "address proof unreadable"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert reject.status_code == 200
    assert reject.json()["status"] == "rejected"

    resubmit = client.post(
        f"/numbers/eligibility-cases/{case_id}/evidence",
        json={"evidence": [{"document_type": "business_address_proof", "note": "clearer scan"}]},
        headers=headers,
    )
    assert resubmit.status_code == 200, resubmit.text
    assert resubmit.json()["status"] == "pending"
    assert len(resubmit.json()["evidence"]) == 1


def test_customer_cannot_submit_evidence_to_another_accounts_eligibility_case(client, db_session, monkeypatch):
    _stub_buy_number(monkeypatch)
    db_session.add(NumberEligibilityRule(country="DE", number_type="local", required_evidence=["business_address_proof"]))
    db_session.commit()

    owner_token = _signup_and_login(client, "eligowner6@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+4930555091116", country="DE")
    client.post("/numbers/purchase", json={"e164": "+4930555091116"}, headers=owner_headers)
    case_id = client.get("/numbers/eligibility-cases", headers=owner_headers).json()[0]["id"]

    intruder_token = _signup_and_login(client, "eligintruder6@example.com")
    intruder_headers = {"Authorization": f"Bearer {intruder_token}"}
    response = client.post(
        f"/numbers/eligibility-cases/{case_id}/evidence",
        json={"evidence": [{"document_type": "business_address_proof"}]},
        headers=intruder_headers,
    )
    assert response.status_code == 404


def test_non_admin_staff_cannot_manage_eligibility_rules(client, db_session):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(
        db_session, email="eligsupport@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT,
    )
    token = client.post(
        "/staff/login", json={"email": "eligsupport@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.put(
        "/staff/number-eligibility-rules",
        json={"country": "FR", "number_type": "local", "required_evidence": [], "is_active": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_deactivating_an_eligibility_rule_unblocks_purchase(client, db_session, monkeypatch):
    """Same override behavior as ComplianceRule.is_active - staff can turn
    a requirement off without deleting the rule row."""
    _stub_buy_number(monkeypatch)
    staff_token = _create_super_admin(client, db_session, "eligstaff7@zoikolocal.com")
    staff_headers = {"Authorization": f"Bearer {staff_token}"}

    create = client.put(
        "/staff/number-eligibility-rules",
        json={"country": "FR", "number_type": "local", "required_evidence": ["id_proof"], "is_active": True},
        headers=staff_headers,
    )
    assert create.status_code == 200

    token = _signup_and_login(client, "eligbuyer8@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+33142685099", country="FR")
    blocked = client.post("/numbers/purchase", json={"e164": "+33142685099"}, headers=headers)
    assert blocked.status_code == 403

    deactivate = client.put(
        "/staff/number-eligibility-rules",
        json={"country": "FR", "number_type": "local", "required_evidence": ["id_proof"], "is_active": False},
        headers=staff_headers,
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False

    unblocked = client.post("/numbers/purchase", json={"e164": "+33142685099"}, headers=headers)
    assert unblocked.status_code == 200, unblocked.text
