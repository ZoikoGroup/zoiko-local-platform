from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.integrations.notifications.email import EmailError, send_email
from app.notifications.models import NotificationDelivery, NotificationDeliveryStatus, NotificationTemplate


class NotificationTemplateMissingError(Exception):
    """Raised when an event fires with no matching row in
    notification_templates - a missing template must be a loud failure,
    not a silently-skipped email."""


def send_notification(
    db: Session,
    *,
    event_name: str,
    recipient_email: str,
    context: dict,
    account_id: str | None = None,
) -> NotificationDelivery:
    """The one pipeline every domain event goes through, instead of each
    call site building its own hardcoded subject/body string: resolve the
    template, render it, send it, and record what happened either way -
    the doc's "domain services publish events; they do not render or send
    email directly" principle, sized for what this platform actually
    sends today."""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.key == event_name).first()
    if template is None:
        raise NotificationTemplateMissingError(f"No notification template registered for event {event_name!r}")

    subject = template.subject_template.format(**context)
    body = template.body_template.format(**context)

    delivery = NotificationDelivery(
        account_id=account_id,
        event_name=event_name,
        recipient_email=recipient_email,
        subject=subject,
        status=NotificationDeliveryStatus.SENT,
    )
    try:
        send_email(to=recipient_email, subject=subject, body=body)
    except EmailError as e:
        delivery.status = NotificationDeliveryStatus.FAILED
        delivery.error = str(e)

    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    log_event(
        db,
        actor="system",
        action="notification.sent" if delivery.status == NotificationDeliveryStatus.SENT else "notification.failed",
        target=f"notification_delivery:{delivery.id}",
        after={"event_name": event_name, "recipient_email": recipient_email, "status": delivery.status},
    )
    return delivery


def list_account_notifications(db: Session, account_id: str) -> list[NotificationDelivery]:
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.desc())
        .all()
    )


def notify_number_activated(db: Session, *, account_id: str, account_email: str, e164: str) -> None:
    send_notification(
        db,
        event_name="number.activated",
        account_id=account_id,
        recipient_email=account_email,
        context={"e164": e164},
    )


def notify_number_suspended(
    db: Session, *, account_id: str, account_email: str, e164: str, reason: str | None = None
) -> None:
    send_notification(
        db,
        event_name="number.suspended",
        account_id=account_id,
        recipient_email=account_email,
        context={"e164": e164, "reason_line": f" Reason: {reason}" if reason else ""},
    )


def notify_compliance_case_approved(
    db: Session, *, account_id: str, account_email: str, jurisdiction: str, requirement_type: str
) -> None:
    send_notification(
        db,
        event_name="compliance.approved",
        account_id=account_id,
        recipient_email=account_email,
        context={"jurisdiction": jurisdiction, "requirement_type": requirement_type.replace("_", " ")},
    )


def notify_compliance_case_rejected(
    db: Session,
    *,
    account_id: str,
    account_email: str,
    jurisdiction: str,
    requirement_type: str,
    reason: str | None = None,
) -> None:
    send_notification(
        db,
        event_name="compliance.rejected",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "jurisdiction": jurisdiction,
            "requirement_type": requirement_type.replace("_", " "),
            "reason_line": f" Reason: {reason}" if reason else "",
        },
    )


def notify_team_member_added(
    db: Session, *, account_id: str, member_email: str, account_name: str, role: str
) -> None:
    send_notification(
        db,
        event_name="team_member.added",
        account_id=account_id,
        recipient_email=member_email,
        context={"account_name": account_name, "role": role},
    )
