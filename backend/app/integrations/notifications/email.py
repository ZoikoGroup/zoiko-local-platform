"""
Provider Gateway for outbound email (notifications category). No SMTP
provider has been chosen yet, so this sends via SMTP when credentials are
configured and otherwise logs to the console — keeps the interface real
without blocking on picking a vendor (Stage 8 stub, per the roadmap).
"""

import logging
import smtplib
from email.message import EmailMessage

from app.core.config import settings

logger = logging.getLogger("zoiko.notifications")


class EmailError(Exception):
    """Raised instead of letting an smtplib-specific exception escape this module."""


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.smtp_host:
        logger.info("EMAIL (no SMTP configured) to=%s subject=%r body=%r", to, subject, body)
        return

    message = EmailMessage()
    message["From"] = settings.smtp_from_email
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as smtp:
            smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except smtplib.SMTPException as e:
        raise EmailError(str(e)) from e
