import logging

from app.compliance.models import ComplianceRule


def _signup_and_login(client, email: str, account_type: str = "individual") -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Numbers Test Co",
            "account_type": account_type,
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _reserve(client, headers, e164: str, country: str = "US"):
    response = client.post("/numbers/reserve", json={"e164": e164, "country": country}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _stub_buy_number(monkeypatch):
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164: {"sid": "PN_fake_sid", "phone_number": e164, "capabilities": {}},
    )


def test_purchase_succeeds_when_no_compliance_rule_is_active_for_the_country(client, monkeypatch):
    """No active kyc_individual/kyc_business rule for 'US' in this test's
    isolated transaction, so the compliance gate must be a no-op — purchasing
    a number in a country with no rules configured must not be blocked."""
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "buyer1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550001111")

    response = client.post("/numbers/purchase", json={"e164": "+15550001111"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_purchase_is_blocked_without_an_approved_compliance_case(client, db_session, monkeypatch):
    """Once a country has an active KYC rule, purchase must be refused until
    the account has an approved compliance case for it — the docs' "Compliance
    Pending" lifecycle state, enforced at the point of purchase."""
    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "buyer2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+442079460001", country="GB")

    response = client.post("/numbers/purchase", json={"e164": "+442079460001"}, headers=headers)
    assert response.status_code == 403
    assert "compliance case" in response.json()["detail"]


def test_purchase_succeeds_once_compliance_case_is_approved(client, db_session, monkeypatch):
    from app.staff import service as staff_service

    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "buyer3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+442079460002", country="GB")

    case_response = client.post(
        "/compliance/cases", json={"jurisdiction": "GB", "requirement_type": "kyc_individual"}, headers=headers
    )
    case_id = case_response.json()["id"]

    staff_service.create_staff(db_session, email="staffbuyer3@zoikolocal.com", password="staffpass123")
    staff_token = client.post(
        "/staff/login", json={"email": "staffbuyer3@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]
    approve = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert approve.status_code == 200

    response = client.post("/numbers/purchase", json={"e164": "+442079460002"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"


def test_suspending_a_number_notifies_the_account_owner(client, monkeypatch, caplog):
    _stub_buy_number(monkeypatch)
    token = _signup_and_login(client, "notifysuspend@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+15550006666")
    client.post("/numbers/purchase", json={"e164": "+15550006666"}, headers=headers)

    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        response = client.post("/numbers/+15550006666/suspend", headers=headers)
    assert response.status_code == 200

    assert any(
        "notifysuspend@example.com" in record.message and "+15550006666" in record.message
        for record in caplog.records
    )


def _add_member(client, admin_headers, email: str) -> str:
    response = client.post(
        "/team/members",
        json={"email": email, "password": "supersecret123", "role": "member"},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_member_only_sees_numbers_assigned_to_them(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner1@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550002222")
    client.post("/numbers/purchase", json={"e164": "+15550002222"}, headers=owner_headers)

    member_id = _add_member(client, owner_headers, "assignmember1@example.com")
    member_token = client.post(
        "/auth/login", json={"email": "assignmember1@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    # unassigned - member sees nothing yet
    assert client.get("/numbers", headers=member_headers).json() == []

    assign = client.put(
        "/numbers/+15550002222/assign", json={"user_id": member_id}, headers=owner_headers
    )
    assert assign.status_code == 200, assign.text
    assert assign.json()["assigned_user_id"] == member_id

    listed = client.get("/numbers", headers=member_headers).json()
    assert [n["e164"] for n in listed] == ["+15550002222"]

    # owner still sees it regardless of assignment
    owner_listed = client.get("/numbers", headers=owner_headers).json()
    assert [n["e164"] for n in owner_listed] == ["+15550002222"]


def test_member_cannot_manage_a_number_not_assigned_to_them(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner2@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550003333")
    client.post("/numbers/purchase", json={"e164": "+15550003333"}, headers=owner_headers)

    _add_member(client, owner_headers, "assignmember2@example.com")
    member_token = client.post(
        "/auth/login", json={"email": "assignmember2@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    response = client.post("/numbers/+15550003333/suspend", headers=member_headers)
    assert response.status_code == 409
    assert "not assigned to you" in response.json()["detail"]


def test_member_can_manage_a_number_once_assigned_to_them(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner3@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550004444")
    client.post("/numbers/purchase", json={"e164": "+15550004444"}, headers=owner_headers)

    member_id = _add_member(client, owner_headers, "assignmember3@example.com")
    client.put("/numbers/+15550004444/assign", json={"user_id": member_id}, headers=owner_headers)

    member_token = client.post(
        "/auth/login", json={"email": "assignmember3@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    response = client.post("/numbers/+15550004444/suspend", headers=member_headers)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "suspended"


def test_member_cannot_assign_numbers(client, monkeypatch):
    _stub_buy_number(monkeypatch)
    owner_token = _signup_and_login(client, "assignowner4@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    _reserve(client, owner_headers, "+15550005555")
    client.post("/numbers/purchase", json={"e164": "+15550005555"}, headers=owner_headers)

    member_id = _add_member(client, owner_headers, "assignmember4@example.com")
    member_token = client.post(
        "/auth/login", json={"email": "assignmember4@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.put(
        "/numbers/+15550005555/assign",
        json={"user_id": member_id},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_business_account_is_gated_on_kyc_business_not_kyc_individual(client, db_session, monkeypatch):
    """A business account purchasing in a country that only has a
    kyc_individual rule must not be gated by it — the requirement type has
    to match the account's own type."""
    _stub_buy_number(monkeypatch)
    db_session.add(
        ComplianceRule(country="FR", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "buyer4@example.com", account_type="business")
    headers = {"Authorization": f"Bearer {token}"}
    _reserve(client, headers, "+33140000001", country="FR")

    response = client.post("/numbers/purchase", json={"e164": "+33140000001"}, headers=headers)
    assert response.status_code == 200, response.text
