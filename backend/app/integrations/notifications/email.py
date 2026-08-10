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


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.resend_api_key:
        logger.info("EMAIL (no Resend API key configured) to=%s subject=%r body=%r", to, subject, body)
        return

    def _primary() -> None:
        try:
            with trace_provider_call("resend", "send_email"):
                response = httpx.post(
                    _RESEND_API_URL,
                    headers={"Authorization": f"Bearer {settings.resend_api_key}"},
                    json={
                        "from": settings.email_from_address,
                        "to": [to],
                        "subject": subject,
                        "text": body,
                    },
                    timeout=15.0,
                )
                response.raise_for_status()
        except httpx.HTTPError as e:
            raise EmailError(f"Resend send failed: {e}") from e

    secondary_fn = (lambda: secondary.send_email(to, subject, body)) if settings.email_failover_enabled else None
    with_failover(_breaker, _primary, secondary_fn, EmailError)
