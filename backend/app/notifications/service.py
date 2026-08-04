from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.events.service import publish_notification_sent
from app.integrations.notifications.email import EmailError, send_email
from app.integrations.notifications.webpush import PushError, PushSubscriptionExpiredError, send_push
from app.integrations.telecom.twilio import TelecomError, send_sms
from app.notifications.models import (
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationTemplate,
    PushSubscription,
)


class NotificationTemplateMissingError(Exception):
    """Raised when an event fires with no matching row in
    notification_templates - a missing template must be a loud failure,
    not a silently-skipped email."""


class SmsTemplateMissingError(Exception):
    """Raised when SMS is requested for an event whose template has no
    sms_body_template - most templates are email-only on purpose (see
    NotificationTemplate.sms_body_template's docstring)."""


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
    publish_notification_sent(account_id, event_name=event_name, channel="email", status=delivery.status.value)

    if account_id:
        _fan_out_push(db, account_id=account_id, event_name=event_name, title=subject, body=body)

    return delivery


def _fan_out_push(db: Session, *, account_id: str, event_name: str, title: str, body: str) -> None:
    """Best-effort push to every device subscribed for this account - a
    missing/expired subscription or a real push-service failure must never
    break the (already-sent) email notification, same rationale as SMS's
    try/except in notify_number_suspended."""
    subscriptions = db.query(PushSubscription).filter(PushSubscription.account_id == account_id).all()
    for subscription in subscriptions:
        delivery = NotificationDelivery(
            account_id=account_id,
            event_name=event_name,
            channel=NotificationChannel.PUSH,
            push_subscription_id=subscription.id,
            subject=title[:255],
            status=NotificationDeliveryStatus.SENT,
        )
        try:
            send_push(
                endpoint=subscription.endpoint, p256dh=subscription.p256dh, auth=subscription.auth,
                title=title, body=body,
            )
        except PushSubscriptionExpiredError:
            delivery.status = NotificationDeliveryStatus.FAILED
            delivery.error = "Subscription expired"
            db.delete(subscription)
        except PushError as e:
            delivery.status = NotificationDeliveryStatus.FAILED
            delivery.error = str(e)

        db.add(delivery)
        db.commit()

        log_event(
            db, actor="system",
            action="notification.sent" if delivery.status == NotificationDeliveryStatus.SENT else "notification.failed",
            target=f"notification_delivery:{delivery.id}",
            after={"event_name": event_name, "channel": "push", "status": delivery.status},
        )
        publish_notification_sent(account_id, event_name=event_name, channel="push", status=delivery.status.value)


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
        action="notification.sent" if delivery.status == NotificationDeliveryStatus.SENT else "notification.failed",
        target=f"notification_delivery:{delivery.id}",
        after={"event_name": event_name, "recipient_phone": recipient_phone, "status": delivery.status},
    )
    publish_notification_sent(account_id, event_name=event_name, channel="sms", status=delivery.status.value)
    return delivery


class NotificationNotFoundError(Exception):
    """Raised when a notification is looked up by id but doesn't belong to
    the requesting account - kept distinct from a generic 404 so callers
    can't accidentally leak another account's notification ids."""


def list_account_notifications(db: Session, account_id: str) -> list[NotificationDelivery]:
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.desc())
        .all()
    )


def count_unread_notifications(db: Session, account_id: str) -> int:
    return (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id, NotificationDelivery.read_at.is_(None))
        .count()
    )


def mark_notification_read(db: Session, account_id: str, notification_id: str) -> NotificationDelivery:
    delivery = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.id == notification_id, NotificationDelivery.account_id == account_id)
        .first()
    )
    if delivery is None:
        raise NotificationNotFoundError(f"No notification {notification_id!r} for this account")

    if delivery.read_at is None:
        delivery.read_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(delivery)
        log_event(
            db, actor_id=account_id, action="notification.read",
            target_type="notification_delivery", target_id=delivery.id,
        )
    return delivery


def mark_all_notifications_read(db: Session, account_id: str) -> int:
    unread = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id, NotificationDelivery.read_at.is_(None))
        .all()
    )
    now = datetime.now(timezone.utc)
    for delivery in unread:
        delivery.read_at = now
    db.commit()

    if unread:
        log_event(
            db, actor_id=account_id, action="notification.read_all",
            target_type="account", target_id=account_id, metadata={"count": len(unread)},
        )
    return len(unread)


def subscribe_to_push(
    db: Session, *, account_id: str, user_id: str, endpoint: str, p256dh: str, auth: str
) -> PushSubscription:
    """Upsert on endpoint - a browser re-subscribing (e.g. after clearing
    storage) sends the same or a fresh endpoint; either way there should be
    exactly one row per live endpoint, not an ever-growing pile of stale
    ones from the same device."""
    existing = db.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).first()
    if existing:
        existing.account_id = account_id
        existing.user_id = user_id
        existing.p256dh = p256dh
        existing.auth = auth
        db.commit()
        db.refresh(existing)
        return existing

    subscription = PushSubscription(
        account_id=account_id, user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth,
    )
    db.add(subscription)
    db.commit()
    db.refresh(subscription)

    log_event(
        db, actor_id=account_id, action="push_subscription.created",
        target_type="push_subscription", target_id=subscription.id,
    )
    return subscription


def unsubscribe_from_push(db: Session, *, account_id: str, endpoint: str) -> bool:
    subscription = (
        db.query(PushSubscription)
        .filter(PushSubscription.account_id == account_id, PushSubscription.endpoint == endpoint)
        .first()
    )
    if subscription is None:
        return False

    subscription_id = subscription.id
    db.delete(subscription)
    db.commit()

    log_event(
        db, actor_id=account_id, action="push_subscription.deleted",
        target_type="push_subscription", target_id=subscription_id,
    )
    return True


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
