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


def _is_provider_failure(e: Exception) -> bool:
    """Passed as with_failover's is_breaker_failure - _breaker is a single
    process-wide instance shared by every outbound email on the platform,
    so what counts as a "failure" here matters beyond just this one
    request. Every EmailError raised in this module wraps the original
    httpx exception via `from e`, so e.__cause__ is that original
    exception.

    An httpx.HTTPStatusError carries the real HTTP status Resend returned
    via `.response.status_code`. A 4xx means Resend understood and rejected
    THIS specific request (e.g. a malformed/undeliverable recipient
    address) - an expected, per-request outcome that says nothing about
    whether Resend itself is healthy. An httpx.RequestError (timeout,
    connection failure, DNS failure, ...) has no `.response` at all -
    nothing HTTP to inspect, which does count as a real provider-health
    signal. Only a 5xx (or no status at all) should trip the shared breaker
    - same conservative default as twilio.py's _is_provider_failure."""
    cause = getattr(e, "__cause__", None)
    status = getattr(getattr(cause, "response", None), "status_code", None)
    return status is None or status >= 500


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


def send_email(
    to: str,
    subject: str,
    body: str,
    headers: dict[str, str] | None = None,
    html: str | None = None,
    from_address: str | None = None,
) -> str | None:
    """Returns Resend's own email id (None in stub/no-API-key mode) - the
    Email Communications System doc's delivery ledger needs it to match a
    later bounce/complaint/delivered webhook event back to the
    NotificationDelivery row it's about (see
    notifications.service.handle_resend_webhook).

    headers: raw RFC 5322 headers (e.g. RFC 8058's List-Unsubscribe /
    List-Unsubscribe-Post) - a body-text unsubscribe link alone doesn't
    trigger Gmail/Yahoo's one-click "Unsubscribe" button, only these
    headers do (doc §4.1/§11, A-23 BLOCKER).

    html: optional rendered HTML alternative (see
    notifications.service._render_html_email). `body` (plain text) is
    always sent too - Resend delivers both parts in one multipart email,
    so mail clients that prefer plain text still get it.

    from_address: category-specific sender (see
    notifications.service._resolve_from_address) - defaults to the single
    settings.email_from_address for any caller that doesn't need
    per-category separation (e.g. send_internal_alert's staff emails)."""
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
                    "from": from_address or settings.email_from_address,
                    "to": [to],
                    "subject": subject,
                    "text": body,
                }
                if html:
                    payload["html"] = html
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

    secondary_fn = (
        (lambda: secondary.send_email(to, subject, body, headers, html, from_address))
        if settings.email_failover_enabled
        else None
    )
    return with_failover(_breaker, _primary, secondary_fn, EmailError, _is_provider_failure)
