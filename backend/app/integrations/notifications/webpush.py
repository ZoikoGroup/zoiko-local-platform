"""
Provider Gateway for outbound Web Push (notifications category). Only file
allowed to call pywebpush directly. Falls back to logging when no VAPID
keypair is configured, matching this codebase's other providers.
"""

import json
import logging

from pywebpush import WebPushException, webpush

from app.core.config import settings

logger = logging.getLogger("zoiko.notifications")


class PushError(Exception):
    """Raised instead of letting a pywebpush-specific exception escape this module."""


class PushSubscriptionExpiredError(PushError):
    """The push service reported this subscription as gone (HTTP 410/404) -
    the caller should delete it rather than keep retrying a dead endpoint."""


def health_check() -> dict:
    return {"configured": bool(settings.vapid_public_key and settings.vapid_private_key), "ok": True, "detail": None}


def send_push(*, endpoint: str, p256dh: str, auth: str, title: str, body: str) -> None:
    if not (settings.vapid_public_key and settings.vapid_private_key):
        logger.info("PUSH (no VAPID keypair configured) endpoint=%s title=%r body=%r", endpoint, title, body)
        return

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
            raise PushSubscriptionExpiredError(f"Push subscription no longer valid: {e}") from e
        raise PushError(f"Web push send failed: {e}") from e
