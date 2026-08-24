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


def send_email(
    to: str,
    subject: str,
    body: str,
    headers: dict[str, str] | None = None,
    html: str | None = None,
    from_address: str | None = None,
) -> str | None:
    if not settings.sendgrid_api_key:
        raise EmailError("Secondary email provider (SendGrid) is not configured - set SENDGRID_API_KEY")

    content = [{"type": "text/plain", "value": body}]
    if html:
        content.append({"type": "text/html", "value": html})

    payload = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": from_address or settings.email_from_address},
        "subject": subject,
        "content": content,
    }
    if headers:
        payload["headers"] = headers

    try:
        response = httpx.post(
            _SEND_URL,
            headers={"Authorization": f"Bearer {settings.sendgrid_api_key}"},
            json=payload,
            timeout=15.0,
        )
        response.raise_for_status()
        # SendGrid doesn't return an id in the body - it's in the
        # X-Message-Id response header. No webhook correlation for the
        # secondary provider yet (see this module's docstring: untested
        # against a live account).
        return response.headers.get("X-Message-Id")
    except httpx.HTTPError as e:
        raise EmailError(f"SendGrid send failed: {e}") from e
