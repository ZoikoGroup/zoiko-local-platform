import pytest

from app.core.startup_checks import assert_jwt_secret_is_configured, parse_allowed_origins


def test_placeholder_jwt_secret_is_allowed_in_development():
    assert_jwt_secret_is_configured("development", "change-me-in-real-env")


def test_placeholder_jwt_secret_is_rejected_outside_development():
    with pytest.raises(RuntimeError, match="placeholder"):
        assert_jwt_secret_is_configured("production", "change-me-in-real-env")


def test_real_jwt_secret_is_accepted_outside_development():
    assert_jwt_secret_is_configured("production", "a-real-randomly-generated-secret")


def test_parse_allowed_origins_splits_and_trims():
    assert parse_allowed_origins("https://app.example.com, https://staging.example.com") == [
        "https://app.example.com",
        "https://staging.example.com",
    ]


def test_parse_allowed_origins_drops_empty_entries():
    assert parse_allowed_origins("https://app.example.com,,") == ["https://app.example.com"]


def test_parse_allowed_origins_rejects_wildcard():
    with pytest.raises(RuntimeError, match="cannot include"):
        parse_allowed_origins("*")


def test_parse_allowed_origins_rejects_wildcard_mixed_with_real_origins():
    with pytest.raises(RuntimeError, match="cannot include"):
        parse_allowed_origins("https://app.example.com,*")
