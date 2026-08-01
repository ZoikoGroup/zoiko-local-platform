from app.integrations.notifications.email import send_email


def notify_number_activated(account_email: str, e164: str) -> None:
    send_email(
        to=account_email,
        subject=f"{e164} is active on Zoiko Local",
        body=f"Your number {e164} is now active. You can start making and receiving calls.",
    )


def notify_number_suspended(account_email: str, e164: str, reason: str | None = None) -> None:
    body = f"Your number {e164} has been suspended."
    if reason:
        body += f" Reason: {reason}"
    send_email(to=account_email, subject=f"{e164} has been suspended", body=body)


def notify_compliance_case_approved(account_email: str, jurisdiction: str, requirement_type: str) -> None:
    send_email(
        to=account_email,
        subject=f"Your {jurisdiction} verification has been approved",
        body=(
            f"Good news — your {requirement_type.replace('_', ' ')} verification for {jurisdiction} "
            "has been approved. You can now purchase numbers in that country."
        ),
    )


def notify_compliance_case_rejected(
    account_email: str, jurisdiction: str, requirement_type: str, reason: str | None = None
) -> None:
    body = f"Your {requirement_type.replace('_', ' ')} verification for {jurisdiction} was not approved."
    if reason:
        body += f" Reason: {reason}"
    body += " Please submit updated documents to try again."
    send_email(to=account_email, subject=f"Your {jurisdiction} verification needs attention", body=body)


def notify_team_member_added(member_email: str, account_name: str, role: str) -> None:
    send_email(
        to=member_email,
        subject=f"You've been added to {account_name} on Zoiko Local",
        body=f"You've been added to {account_name} as {role}. Sign in with the email and password you were given.",
    )
