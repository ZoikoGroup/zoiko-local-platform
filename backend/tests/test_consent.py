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
