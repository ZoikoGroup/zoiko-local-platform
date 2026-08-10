"""
Chaos/failure testing (Roadmap Month 5 launch-readiness gate) - simulates
real provider/infrastructure outages against actual code paths and confirms
the app degrades gracefully (clean error status, no crash, no corrupted
partial state) instead of leaking an unhandled 500 or half-committing data.
"""

from sqlalchemy.exc import OperationalError


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Chaos Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _simulate_db_outage(db_session, monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise OperationalError(
            "SELECT 1", {}, Exception("connection to server at ... failed: server closed the connection")
        )

    monkeypatch.setattr(db_session, "query", _raise)


def test_db_outage_on_a_read_endpoint_returns_a_clean_503(client, db_session, monkeypatch):
    """Found via load testing: a real Postgres outage mid-request used to
    bubble up as a bare, unhandled 500 on every plain list/read endpoint -
    see app.main's DBAPIError handler."""
    token = _signup_and_login(client, "chaosdb1@example.com")

    _simulate_db_outage(db_session, monkeypatch)

    response = client.get("/numbers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    assert "temporarily unavailable" in response.json()["detail"].lower()


def test_db_outage_on_a_write_endpoint_returns_a_clean_503(client, db_session, monkeypatch):
    token = _signup_and_login(client, "chaosdb2@example.com")

    _simulate_db_outage(db_session, monkeypatch)

    response = client.post(
        "/numbers/reserve",
        json={"e164": "+15550001234", "country": "US"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503


def test_db_outage_does_not_leak_internal_error_details(client, db_session, monkeypatch):
    """A raw DB error can contain connection strings/hostnames - the client
    response must never include str(exc), only the fixed generic message."""
    token = _signup_and_login(client, "chaosdb3@example.com")

    def _raise_with_sensitive_detail(*args, **kwargs):
        raise OperationalError(
            "SELECT 1", {}, Exception("password authentication failed for user \"zoiko\" at host secret-db.internal")
        )

    monkeypatch.setattr(db_session, "query", _raise_with_sensitive_detail)

    response = client.get("/numbers", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 503
    body = response.text
    assert "secret-db.internal" not in body
    assert "password authentication" not in body
