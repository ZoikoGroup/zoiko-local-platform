from app.compliance.models import ComplianceRule


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Compliance Test Co",
            "account_type": "individual",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def test_list_rules_for_a_country(client, db_session):
    db_session.add(
        ComplianceRule(
            country="US",
            requirement_type="kyc_individual",
            required_documents=["government_id"],
        )
    )
    db_session.commit()

    response = client.get("/compliance/rules?country=US")
    assert response.status_code == 200
    assert any(r["requirement_type"] == "kyc_individual" for r in response.json())


def test_list_rules_for_country_with_no_rules_returns_empty(client):
    response = client.get("/compliance/rules?country=ZZ")
    assert response.status_code == 200
    assert response.json() == []


def test_open_compliance_case_requires_auth(client):
    response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "US", "requirement_type": "kyc_individual"},
    )
    assert response.status_code == 401


def test_open_and_list_compliance_case(client):
    token = _signup_and_login(client, "compliance1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "us", "requirement_type": "kyc_individual"},
        headers=headers,
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["jurisdiction"] == "US"  # normalized to uppercase
    assert body["status"] == "pending"

    list_response = client.get("/compliance/cases/me", headers=headers)
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_opening_a_case_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    token = _signup_and_login(client, "compliance2@example.com")
    case_response = client.post(
        "/compliance/cases",
        json={"jurisdiction": "GB", "requirement_type": "kyc_individual"},
        headers={"Authorization": f"Bearer {token}"},
    )
    case_id = case_response.json()["id"]

    events = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.action == "compliance.case_opened",
            AuditEvent.target == f"compliance_case:{case_id}",
        )
        .all()
    )
    assert len(events) == 1


def _open_case(client, headers, jurisdiction="US") -> str:
    response = client.post(
        "/compliance/cases",
        json={"jurisdiction": jurisdiction, "requirement_type": "kyc_individual"},
        headers=headers,
    )
    return response.json()["id"]


def test_submit_document_adds_it_to_the_case(client):
    token = _signup_and_login(client, "docsubmit1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(
        f"/compliance/cases/{case_id}/documents",
        json={"document_type": "government_id", "reference": "placeholder-ref-1"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["documents"] == [
        {"document_type": "government_id", "reference": "placeholder-ref-1"}
    ]


def test_submit_document_on_someone_elses_case_is_forbidden(client):
    token_a = _signup_and_login(client, "docowner@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {token_a}"})

    token_b = _signup_and_login(client, "docintruder@example.com")
    response = client.post(
        f"/compliance/cases/{case_id}/documents",
        json={"document_type": "government_id", "reference": "x"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert response.status_code == 403


def test_submit_document_on_missing_case_is_404(client):
    token = _signup_and_login(client, "docsubmit2@example.com")
    response = client.post(
        "/compliance/cases/does-not-exist/documents",
        json={"document_type": "government_id", "reference": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_owner_can_approve_a_case(client):
    token = _signup_and_login(client, "approve1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(f"/compliance/cases/{case_id}/approve", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_owner_can_reject_a_case_with_a_reason(client):
    token = _signup_and_login(client, "reject1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)

    response = client.post(
        f"/compliance/cases/{case_id}/reject",
        json={"reason": "Document was blurry"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_approve_requires_authentication(client):
    token = _signup_and_login(client, "approve2@example.com")
    case_id = _open_case(client, {"Authorization": f"Bearer {token}"})

    response = client.post(f"/compliance/cases/{case_id}/approve")
    assert response.status_code == 401


def test_approving_a_case_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    token = _signup_and_login(client, "approve3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_id = _open_case(client, headers)
    client.post(f"/compliance/cases/{case_id}/approve", headers=headers)

    events = (
        db_session.query(AuditEvent)
        .filter(
            AuditEvent.action == "compliance.case_approved",
            AuditEvent.target == f"compliance_case:{case_id}",
        )
        .all()
    )
    assert len(events) == 1
