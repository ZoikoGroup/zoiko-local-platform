from app.integrations.notifications.email import send_email


def notify_number_activated(account_email: str, e164: str) -> None:
    send_email(
        to=account_email,
        subject=f"{e164} is active on Zoiko Local",
        body=f"Your number {e164} is now active. You can start making and receiving calls.",
    )
