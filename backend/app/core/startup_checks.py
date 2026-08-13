"""
Fail-fast checks run once at app startup (see app/main.py). Kept as plain
functions, separate from the module-level code that calls them, so they're
unit-testable without needing to re-import app.main under different settings.
"""

import logging

from app.core.config import PLACEHOLDER_JWT_SECRET_KEY

logger = logging.getLogger("zoiko.startup")


def assert_jwt_secret_is_configured(environment: str, jwt_secret_key: str) -> None:
    """A real deployment must set its own JWT_SECRET_KEY - silently signing
    JWTs with the value checked into this repo's own .env.example would be a
    forgeable-token vulnerability, not a theoretical one."""
    if environment != "development" and jwt_secret_key == PLACEHOLDER_JWT_SECRET_KEY:
        raise RuntimeError(
            "JWT_SECRET_KEY is still the placeholder value - set a real random secret "
            "in this environment's configuration before starting outside development."
        )


def warn_if_db_connection_budget_is_risky(
    pool_size: int, max_overflow: int, web_concurrency: int, warning_threshold: int
) -> None:
    """Advisory only (a log line, not a RuntimeError) - unlike the JWT check
    above, there's no single correct number here: it depends on the actual
    Neon plan's pooled connection limit, which this process has no way to
    know. What IS knowable from inside a single worker process: its own
    engine's ceiling (pool_size + max_overflow) times how many sibling
    workers share this same database (web_concurrency) - each worker gets
    its own independent SQLAlchemy pool, so the real footprint is per
    worker, multiplied. Flagging it here beats only discovering it via a
    wall of "too many connections" errors once traffic actually hits it."""
    total = (pool_size + max_overflow) * web_concurrency
    if total > warning_threshold:
        logger.warning(
            "DB connection budget may be risky: %d workers x (pool_size=%d + max_overflow=%d) "
            "= %d possible connections from this instance alone, above the configured warning "
            "threshold of %d. Confirm this fits within your Neon plan's pooled connection limit, "
            "especially before running more than one instance.",
            web_concurrency, pool_size, max_overflow, total, warning_threshold,
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
