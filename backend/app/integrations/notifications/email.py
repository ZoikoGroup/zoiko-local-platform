"""
Provider Gateway for outbound email (notifications category). Only file
allowed to call Resend's API directly. Falls back to logging when no API
key is configured, so the interface stays real without ever blocking
local dev on real credentials.
"""

import logging

import httpx

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover
from app.observability.service import trace_provider_call

logger = logging.getLogger("zoiko.notifications")

_RESEND_API_URL = "https://api.resend.com/emails"

_breaker = CircuitBreaker("email")


def circuit_state() -> str:
    return _breaker.state.value


class EmailError(Exception):
    """Raised instead of letting an httpx/Resend-specific exception escape this module."""


# Imported after EmailError is defined - _secondary_stub imports it back
# from this module, which would otherwise be a circular import.
from app.integrations.notifications import _email_secondary_stub as secondary  # noqa: E402


def health_check() -> dict:
    """Confirmed live: a properly-scoped "sending access" API key (the
    least-privilege, recommended kind) gets a 401 from /domains - Resend
    has no lightweight endpoint that a sending-only key can call, and a
    401 from that key is indistinguishable from an actually-invalid one.
    Rather than report a false "broken" signal for the more secure key
    type, this only checks presence - the send path itself is exercised
    for real every time an email actually goes out."""
    if not settings.resend_api_key:
        return {"configured": False, "ok": False, "detail": None}
    return {"configured": True, "ok": True, "detail": None}


def send_email(to: str, subject: str, body: str, headers: dict[str, str] | None = None) -> str | None:
    """Returns Resend's own email id (None in stub/no-API-key mode) - the
    Email Communications System doc's delivery ledger needs it to match a
    later bounce/complaint/delivered webhook event back to the
    NotificationDelivery row it's about (see
    notifications.service.handle_resend_webhook).

    headers: raw RFC 5322 headers (e.g. RFC 8058's List-Unsubscribe /
    List-Unsubscribe-Post) - a body-text unsubscribe link alone doesn't
    trigger Gmail/Yahoo's one-click "Unsubscribe" button, only these
    headers do (doc §4.1/§11, A-23 BLOCKER)."""
    if not settings.resend_api_key:
        logger.info("EMAIL (no Resend API key configured) to=%s subject=%r body=%r headers=%r", to, subject, body, headers)
        return None

    # Email Communications System doc A-46 (BLOCKER) "environment
    # isolation" - a real API key present in dev/staging used to mean any
    # address passed through this function got a real email, with the only
    # guard being "is a key configured" rather than "is this production."
    # notification_test_recipients is a small comma-separated allowlist
    # (your own inbox during testing) that still gets real sends outside
    # production; everyone else gets logged instead, same shape as the
    # no-API-key branch above.
    allowlist = {addr.strip().lower() for addr in settings.notification_test_recipients.split(",") if addr.strip()}
    if settings.environment != "production" and to.strip().lower() not in allowlist:
        logger.info(
            "EMAIL (non-production environment, %r not on notification_test_recipients allowlist) "
            "to=%s subject=%r body=%r headers=%r",
            settings.environment, to, subject, body, headers,
        )
        return None

    def _primary() -> str | None:
        try:
            with trace_provider_call("resend", "send_email"):
                payload = {
                    "from": settings.email_from_address,
                    "to": [to],
                    "subject": subject,
                    "text": body,
                }
                if headers:
                    payload["headers"] = headers
                response = httpx.post(
                    _RESEND_API_URL,
                    headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                    json=payload,
                    timeout=15.0,
                )
                response.raise_for_status()
                return response.json().get("id")
        except httpx.HTTPError as e:
            raise EmailError(f"Resend send failed: {e}") from e

    secondary_fn = (lambda: secondary.send_email(to, subject, body, headers)) if settings.email_failover_enabled else None
    return with_failover(_breaker, _primary, secondary_fn, EmailError)
