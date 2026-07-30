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
    client.post(
        "/compliance/cases",
        json={"jurisdiction": "GB", "requirement_type": "kyc_individual"},
        headers={"Authorization": f"Bearer {token}"},
    )
    events = db_session.query(AuditEvent).filter(AuditEvent.action == "compliance.case_opened").all()
    assert len(events) == 1
