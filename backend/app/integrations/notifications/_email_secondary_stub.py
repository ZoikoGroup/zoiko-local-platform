"""Stand-in for a second transactional email vendor (e.g. SendGrid, Postmark)
behind email_failover_enabled. No real second-vendor account exists yet -
raises a clearly labeled error instead of silently no-opping.
"""

from app.integrations.notifications.email import EmailError

_NOT_CONFIGURED = (
    "secondary email provider not configured - set EMAIL_SECONDARY_* "
    "credentials once a second vendor account exists"
)


def send_email(to: str, subject: str, body: str) -> None:
    raise EmailError(_NOT_CONFIGURED)
