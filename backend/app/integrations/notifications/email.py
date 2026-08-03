"""
Provider Gateway for outbound email (notifications category). Only file
allowed to call Resend's API directly. Falls back to logging when no API
key is configured, so the interface stays real without ever blocking
local dev on real credentials.
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("zoiko.notifications")

_RESEND_API_URL = "https://api.resend.com/emails"


class EmailError(Exception):
    """Raised instead of letting an httpx/Resend-specific exception escape this module."""


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.resend_api_key:
        logger.info("EMAIL (no Resend API key configured) to=%s subject=%r body=%r", to, subject, body)
        return

    try:
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
