def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Compliance Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"]


def test_consent_requires_auth(client):
    assert client.post("/compliance/consent", json={"consent_type": "ai_processing"}).status_code == 401
    assert client.get("/compliance/consent").status_code == 401
    assert client.delete("/compliance/consent/ai_processing").status_code == 401


def test_grant_list_and_revoke_consent(client):
    token = _signup_and_login(client, "complianceuser@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    grant_response = client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers
    )
    assert grant_response.status_code == 200
    assert grant_response.json()["granted_at"]
    assert grant_response.json()["jurisdiction"] == "GLOBAL"  # default when omitted

    list_response = client.get("/compliance/consent", headers=headers)
    assert list_response.status_code == 200
    records = list_response.json()
    assert len(records) == 1
    assert records[0]["consent_type"] == "ai_processing"
    assert records[0]["revoked_at"] is None

    revoke_response = client.delete("/compliance/consent/ai_processing", headers=headers)
    assert revoke_response.status_code == 200
    assert revoke_response.json()["revoked_at"]


def test_revoke_without_prior_grant_returns_404(client):
    token = _signup_and_login(client, "compliancenogrant@example.com")
    response = client.delete(
        "/compliance/consent/ai_processing", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


def test_grant_can_be_scoped_to_a_specific_jurisdiction(client):
    token = _signup_and_login(client, "compliancejurisdiction1@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    grant_response = client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing", "jurisdiction": "us"},
        headers=headers,
    )
    assert grant_response.status_code == 200
    assert grant_response.json()["jurisdiction"] == "US"  # normalized to uppercase

    list_response = client.get("/compliance/consent", headers=headers)
    records = list_response.json()
    assert len(records) == 1
    assert records[0]["jurisdiction"] == "US"


def test_global_and_country_specific_grants_are_independent_records(client):
    token = _signup_and_login(client, "compliancejurisdiction2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)  # GLOBAL
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing", "jurisdiction": "DE"}, headers=headers
    )

    records = client.get("/compliance/consent", headers=headers).json()
    jurisdictions = {r["jurisdiction"] for r in records}
    assert jurisdictions == {"GLOBAL", "DE"}


def test_revoking_a_specific_jurisdiction_does_not_touch_global_or_others(client):
    token = _signup_and_login(client, "compliancejurisdiction3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)  # GLOBAL
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing", "jurisdiction": "US"}, headers=headers
    )

    revoke_response = client.delete(
        "/compliance/consent/ai_processing?jurisdiction=US", headers=headers
    )
    assert revoke_response.status_code == 200
    assert revoke_response.json()["jurisdiction"] == "US"

    records = {r["jurisdiction"]: r for r in client.get("/compliance/consent", headers=headers).json()}
    assert records["US"]["revoked_at"] is not None
    assert records["GLOBAL"]["revoked_at"] is None


def _make_account(db_session):
    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Jurisdiction Unit Test Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.commit()
    return account


def test_has_active_consent_treats_global_as_covering_every_jurisdiction(db_session):
    """Service-level check: a GLOBAL grant is a superset - the single
    existing "grant consent" button in the product relies on this to keep
    working for every country without any UI changes."""
    from app.consent.models import ConsentType
    from app.consent.service import grant_consent, has_active_consent

    account = _make_account(db_session)

    assert not has_active_consent(db_session, account.id, ConsentType.AI_PROCESSING, "FR")

    grant_consent(db_session, account.id, ConsentType.AI_PROCESSING)  # GLOBAL
    assert has_active_consent(db_session, account.id, ConsentType.AI_PROCESSING, "FR")
    assert has_active_consent(db_session, account.id, ConsentType.AI_PROCESSING, "US")
    assert has_active_consent(db_session, account.id, ConsentType.AI_PROCESSING)  # GLOBAL itself


def test_has_active_consent_country_specific_grant_does_not_cover_other_countries(db_session):
    from app.consent.models import ConsentType
    from app.consent.service import grant_consent, has_active_consent

    account = _make_account(db_session)

    grant_consent(db_session, account.id, ConsentType.AI_PROCESSING, "US")
    assert has_active_consent(db_session, account.id, ConsentType.AI_PROCESSING, "US")
    assert not has_active_consent(db_session, account.id, ConsentType.AI_PROCESSING, "DE")
