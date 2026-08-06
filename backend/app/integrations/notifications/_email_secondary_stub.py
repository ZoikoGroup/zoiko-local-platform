"""Secondary transactional email vendor (SendGrid) behind
email_failover_enabled. Real API calls, not a mock - but NOT tested against
a live account, since no real SendGrid credentials exist yet. Wire
SENDGRID_API_KEY in .env and flip EMAIL_FAILOVER_ENABLED=true to activate.
Callers in email.py never change, since it dispatches to this module by
function name only.
"""

import httpx

from app.core.config import settings
from app.integrations.notifications.email import EmailError

_SEND_URL = "https://api.sendgrid.com/v3/mail/send"


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.sendgrid_api_key:
        raise EmailError("Secondary email provider (SendGrid) is not configured - set SENDGRID_API_KEY")

    try:
        response = httpx.post(
            _SEND_URL,
            headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
            json={
                "personalizations": [{"to": [{"email": to}]}],
                "from": {"email": settings.email_from_address},
                "subject": subject,
                "content": [{"type": "text/plain", "value": body}],
            },
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise EmailError(f"SendGrid send failed: {e}") from e
