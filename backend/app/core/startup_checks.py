"""
Fail-fast checks run once at app startup (see app/main.py). Kept as plain
functions, separate from the module-level code that calls them, so they're
unit-testable without needing to re-import app.main under different settings.
"""

from app.core.config import PLACEHOLDER_JWT_SECRET_KEY


def assert_jwt_secret_is_configured(environment: str, jwt_secret_key: str) -> None:
    """A real deployment must set its own JWT_SECRET_KEY - silently signing
    JWTs with the value checked into this repo's own .env.example would be a
    forgeable-token vulnerability, not a theoretical one."""
    if environment != "development" and jwt_secret_key == PLACEHOLDER_JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY is still the placeholder value - set a real random secret "
            "in this environment's configuration before starting outside development."
        )


def parse_allowed_origins(raw: str) -> list[str]:
    """Comma-separated ALLOWED_ORIGINS -> a CORS origin list. Rejects "*"
    outright: this API is used with allow_credentials=True, and browsers
    reject a wildcard origin combined with credentials anyway - failing fast
    here beats a confusing browser-side CORS error in production."""
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    if "*" in origins:
        raise RuntimeError(
            "ALLOWED_ORIGINS cannot include '*' - this API uses credentialed "
            "requests, and browsers reject a wildcard origin combined with "
            "credentials. List the real deployed frontend origin(s) explicitly instead."
        )
    return origins
