"""
Provider Gateway for outbound Web Push (notifications category). Only file
allowed to call pywebpush directly. Falls back to logging when no VAPID
keypair is configured, matching this codebase's other providers.
"""

import json
import logging

from pywebpush import WebPushException, webpush

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover

logger = logging.getLogger("zoiko.notifications")

_breaker = CircuitBreaker("webpush")


def circuit_state() -> str:
    return _breaker.state.value


class PushError(Exception):
    """Raised instead of letting a pywebpush-specific exception escape this module."""


class PushSubscriptionExpiredError(PushError):
    """The push service reported this subscription as gone (HTTP 410/404) -
    the caller should delete it rather than keep retrying a dead endpoint."""


def _is_provider_failure(e: Exception) -> bool:
    """Passed as with_failover's is_breaker_failure - _breaker is a single
    process-wide instance shared by every web push send on the platform, so
    what counts as a "failure" here matters beyond just this one request.

    PushSubscriptionExpiredError (a PushError subclass raised when the
    push service reports 404/410) is a per-subscription fact - the
    endpoint itself is dead, which says nothing about whether the vendor
    is healthy. Critically, is_breaker_failure returning False here also
    makes with_failover re-raise it immediately with NO failover attempt
    (see with_failover's docstring) - without this, it would flow into the
    same generic PushError failover path as a real outage and get silently
    forwarded to the secondary provider instead of propagating to
    notifications.service's caller, which is what actually prunes the dead
    subscription. No secondary provider would help here either way, since
    the endpoint is the browser's own push service, fixed by the
    subscription itself, not by the vendor sending the push.

    For any other PushError, mirror twilio.py's status-code split: the
    underlying WebPushException carries the real HTTP status via
    `.response.status_code` (accessible off e.__cause__, since every other
    PushError in this module wraps it via `from e`) - a 4xx means the push
    service rejected THIS specific request, a 5xx or no response at all
    (timeout/connection failure) is a real provider-health signal."""
    if isinstance(e, PushSubscriptionExpiredError):
        return False
    cause = getattr(e, "__cause__", None)
    status = getattr(getattr(cause, "response", None), "status_code", None)
    return status is None or status >= 500


# Imported after PushError is defined - _secondary_stub imports it back from
# this module, which would otherwise be a circular import.
from app.integrations.notifications import _webpush_secondary_stub as secondary  # noqa: E402


def health_check() -> dict:
    return {"configured": bool(settings.vapid_public_key and settings.vapid_private_key), "ok": True, "detail": None}


def send_push(*, endpoint: str, p256dh: str, auth: str, title: str, body: str) -> None:
    if not (settings.vapid_public_key and settings.vapid_private_key):
        logger.info("PUSH (no VAPID keypair configured) endpoint=%s title=%r body=%r", endpoint, title, body)
        return

    def _primary() -> None:
        try:
            webpush(
                subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
                data=json.dumps({"title": title, "body": body}),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": f"mailto:{settings.vapid_claim_email}"},
            )
        except WebPushException as e:
            status_code = e.response.status_code if e.response is not None else None
            if status_code in (404, 410):
                # A dead endpoint is a per-subscription fact, not a vendor
                # outage - the breaker still records it as a failure (same
                # as any other category), but no secondary provider would
                # help here either, since the endpoint is the browser's own
                # push service, fixed by the subscription itself.
                raise PushSubscriptionExpiredError(f"Push subscription no longer valid: {e}") from e
            raise PushError(f"Web push send failed: {e}") from e

    secondary_fn = (
        (lambda: secondary.send_push(endpoint=endpoint, p256dh=p256dh, auth=auth, title=title, body=body))
        if settings.webpush_failover_enabled else None
    )
    with_failover(_breaker, _primary, secondary_fn, PushError, _is_provider_failure)
