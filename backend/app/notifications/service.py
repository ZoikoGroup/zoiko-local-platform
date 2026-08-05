from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.integrations.notifications.email import EmailError, send_email
from app.integrations.telecom.twilio import TelecomError, send_sms
from app.notifications.models import (
    NotificationCategory,
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationPreference,
    NotificationPriority,
    NotificationTemplate,
)


class NotificationTemplateMissingError(Exception):
    """Raised when an event fires with no matching row in
    notification_templates - a missing template must be a loud failure,
    not a silently-skipped email."""


class SmsTemplateMissingError(Exception):
    """Raised when SMS is requested for an event whose template has no
    sms_body_template - most templates are email-only on purpose (see
    NotificationTemplate.sms_body_template's docstring)."""


class InvalidTimezoneError(Exception):
    """Raised when a preference update's quiet_hours_timezone isn't a real
    IANA zone name - caught at save time rather than the next time a
    notification tries to use it and crashes instead."""


def get_or_create_preference(db: Session, account_id: str) -> NotificationPreference:
    pref = db.query(NotificationPreference).filter(NotificationPreference.account_id == account_id).first()
    if pref is None:
        pref = NotificationPreference(account_id=account_id)
        db.add(pref)
        db.commit()
        db.refresh(pref)
    return pref


def update_preference(
    db: Session,
    account_id: str,
    *,
    transactional_enabled: bool | None = None,
    sms_enabled: bool | None = None,
    quiet_hours_start: time | None = ...,
    quiet_hours_end: time | None = ...,
    quiet_hours_timezone: str | None = None,
) -> NotificationPreference:
    """Uses `...` (not None) as the "leave unchanged" sentinel for the two
    quiet-hours time fields specifically, since None is itself a meaningful
    value for them (clears quiet hours entirely) - unlike the booleans and
    timezone, which always have a real value worth setting."""
    pref = get_or_create_preference(db, account_id)
    if transactional_enabled is not None:
        pref.transactional_enabled = transactional_enabled
    if sms_enabled is not None:
        pref.sms_enabled = sms_enabled
    if quiet_hours_start is not ...:
        pref.quiet_hours_start = quiet_hours_start
    if quiet_hours_end is not ...:
        pref.quiet_hours_end = quiet_hours_end
    if quiet_hours_timezone is not None:
        try:
            ZoneInfo(quiet_hours_timezone)
        except Exception as e:
            raise InvalidTimezoneError(f"{quiet_hours_timezone!r} is not a valid timezone") from e
        pref.quiet_hours_timezone = quiet_hours_timezone
    db.commit()
    db.refresh(pref)
    return pref


def _is_exempt_from_suppression(template: NotificationTemplate) -> bool:
    """SECURITY-category or CRITICAL-priority templates always send,
    regardless of preference or quiet hours - see NotificationPreference's
    docstring for why."""
    return template.category == NotificationCategory.SECURITY or template.priority == NotificationPriority.CRITICAL


def is_within_quiet_hours(pref: NotificationPreference, *, now: datetime | None = None) -> bool:
    if pref.quiet_hours_start is None or pref.quiet_hours_end is None:
        return False
    now_local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(pref.quiet_hours_timezone)).time()
    start, end = pref.quiet_hours_start, pref.quiet_hours_end
    if start <= end:
        return start <= now_local <= end
    return now_local >= start or now_local <= end  # overnight range, e.g. 22:00-06:00


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

    # A bare account_id=None call (no account context yet, e.g. pre-signup
    # flows) has no preference to check, so it always sends.
    is_opted_out = (
        account_id is not None
        and not _is_exempt_from_suppression(template)
        and not get_or_create_preference(db, account_id).transactional_enabled
    )
    if is_opted_out:
        delivery.status = NotificationDeliveryStatus.SUPPRESSED
    else:
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
        action=f"notification.{delivery.status.value}",
        target=f"notification_delivery:{delivery.id}",
        after={"event_name": event_name, "recipient_email": recipient_email, "status": delivery.status},
    )
    return delivery


def send_sms_notification(
    db: Session,
    *,
    event_name: str,
    recipient_phone: str,
    context: dict,
    account_id: str | None = None,
) -> NotificationDelivery:
    """SMS counterpart to send_notification - same template registry and
    delivery ledger, gated on the template actually having an
    sms_body_template (most don't, by design - see the model docstring)."""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.key == event_name).first()
    if template is None:
        raise NotificationTemplateMissingError(f"No notification template registered for event {event_name!r}")
    if not template.sms_body_template:
        raise SmsTemplateMissingError(f"Event {event_name!r} has no SMS template - email-only")

    body = template.sms_body_template.format(**context)

    delivery = NotificationDelivery(
        account_id=account_id,
        event_name=event_name,
        channel=NotificationChannel.SMS,
        recipient_phone=recipient_phone,
        subject=body[:255],  # SMS has no separate subject line - reuse the body for the ledger's display column
        status=NotificationDeliveryStatus.SENT,
    )

    # SMS is the interruptive channel (buzzes a phone), so unlike email it's
    # also gated on quiet hours, not just the opt-out toggle - both checks
    # skipped for CRITICAL-priority templates.
    pref = get_or_create_preference(db, account_id) if account_id else None
    is_exempt = _is_exempt_from_suppression(template)
    if pref and not is_exempt and not pref.sms_enabled:
        delivery.status = NotificationDeliveryStatus.SUPPRESSED
        delivery.error = "Suppressed: SMS notifications are disabled for this account"
    elif pref and not is_exempt and is_within_quiet_hours(pref):
        delivery.status = NotificationDeliveryStatus.SUPPRESSED
        delivery.error = "Suppressed: sent during the account's configured quiet hours"
    else:
        try:
            send_sms(to=recipient_phone, body=body)
        except TelecomError as e:
            delivery.status = NotificationDeliveryStatus.FAILED
            delivery.error = str(e)

    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    log_event(
        db,
        actor="system",
        action=f"notification.{delivery.status.value}",
        target=f"notification_delivery:{delivery.id}",
        after={"event_name": event_name, "recipient_phone": recipient_phone, "status": delivery.status},
    )
    return delivery


def list_account_notifications(db: Session, account_id: str) -> list[NotificationDelivery]:
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.desc())
        .all()
    )


class NotificationAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the given notification."""


def mark_notification_read(db: Session, account_id: str, notification_id: str) -> NotificationDelivery:
    delivery = db.query(NotificationDelivery).filter(NotificationDelivery.id == notification_id).first()
    if delivery is None or delivery.account_id != account_id:
        raise NotificationAuthorizationError(f"{notification_id} is not a notification on your account")
    if delivery.read_at is None:
        delivery.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(delivery)
    return delivery


def mark_all_notifications_read(db: Session, account_id: str) -> int:
    result = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id, NotificationDelivery.read_at.is_(None))
        .update({NotificationDelivery.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()
    return result


def notify_number_activated(db: Session, *, account_id: str, account_email: str, e164: str) -> None:
    send_notification(
        db,
        event_name="number.activated",
        account_id=account_id,
        recipient_email=account_email,
        context={"e164": e164},
    )


def notify_number_suspended(
    db: Session,
    *,
    account_id: str,
    account_email: str,
    e164: str,
    reason: str | None = None,
    account_phone: str | None = None,
) -> None:
    context = {"e164": e164, "reason_line": f" Reason: {reason}" if reason else ""}
    send_notification(
        db, event_name="number.suspended", account_id=account_id, recipient_email=account_email, context=context
    )

    # Best-effort second channel for this one safety-critical event - a
    # missing phone number, missing SMS template, or a real Twilio SMS
    # failure must never break the (already-sent) email notification.
    if account_phone:
        try:
            send_sms_notification(
                db, event_name="number.suspended", account_id=account_id, recipient_phone=account_phone,
                context=context,
            )
        except (SmsTemplateMissingError, TelecomError):
            pass


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


def notify_porting_request_submitted(db: Session, *, account_id: str, account_email: str, phone_number: str) -> None:
    send_notification(
        db, event_name="porting.submitted", account_id=account_id, recipient_email=account_email,
        context={"phone_number": phone_number},
    )


def notify_porting_request_approved(db: Session, *, account_id: str, account_email: str, phone_number: str) -> None:
    send_notification(
        db, event_name="porting.approved", account_id=account_id, recipient_email=account_email,
        context={"phone_number": phone_number},
    )


def notify_porting_request_rejected(
    db: Session, *, account_id: str, account_email: str, phone_number: str, reason: str | None = None
) -> None:
    send_notification(
        db, event_name="porting.rejected", account_id=account_id, recipient_email=account_email,
        context={"phone_number": phone_number, "reason_line": f" Reason: {reason}" if reason else ""},
    )


def notify_porting_request_completed(db: Session, *, account_id: str, account_email: str, phone_number: str) -> None:
    send_notification(
        db, event_name="porting.completed", account_id=account_id, recipient_email=account_email,
        context={"phone_number": phone_number},
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
