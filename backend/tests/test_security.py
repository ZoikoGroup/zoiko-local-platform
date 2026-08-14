"""Unit tests for app.core.security's JWT helpers - previously untested in
isolation (only exercised indirectly via the many _signup_and_login-style
helpers across the suite). Added alongside the secrets-rotation support in
decode_access_token - see docs/runbooks/secrets-rotation.md."""
from app.core.security import create_access_token, decode_access_token


def test_decode_accepts_a_token_signed_with_the_current_key(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "current-key")
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key_previous", "")

    token = create_access_token("user-123", scope="customer")
    payload = decode_access_token(token)
    assert payload is not None
    assert payload["sub"] == "user-123"
    assert payload["scope"] == "customer"


def test_decode_rejects_a_token_signed_with_an_unknown_key(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "key-a")
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key_previous", "")
    token = create_access_token("user-456")

    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "key-b")
    assert decode_access_token(token) is None


def test_rotation_window_accepts_both_old_and_new_key(monkeypatch):
    """The actual mechanism the secrets-rotation runbook depends on: a
    token issued under the OLD key must keep verifying once the app has
    moved on to signing with a NEW key, as long as the old one is set as
    jwt_secret_key_previous."""
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "old-key")
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key_previous", "")
    old_token = create_access_token("user-789")

    # Rotation happens: new key becomes current, old key moves to _previous.
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "new-key")
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key_previous", "old-key")

    old_payload = decode_access_token(old_token)
    assert old_payload is not None
    assert old_payload["sub"] == "user-789"

    new_token = create_access_token("user-789")
    new_payload = decode_access_token(new_token)
    assert new_payload is not None


def test_after_rotation_window_closes_the_old_key_no_longer_verifies(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "old-key")
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key_previous", "")
    old_token = create_access_token("user-999")

    monkeypatch.setattr("app.core.config.settings.jwt_secret_key", "new-key")
    monkeypatch.setattr("app.core.config.settings.jwt_secret_key_previous", "")  # rotation window closed

    assert decode_access_token(old_token) is None
