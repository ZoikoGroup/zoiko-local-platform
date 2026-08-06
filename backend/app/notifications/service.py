from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.config import settings
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


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _mask_number(e164: str) -> str:
    """Shows enough of a phone number to be recognizable without exposing
    the full digit string in an email - used by the canonical templates'
    {{*.masked_or_formatted}} / {{call.*_masked}} tokens (Email
    Communications System doc's "identify... without exposing unnecessary
    data" standard)."""
    if len(e164) <= 6:
        return e164
    return f"{e164[:2]}{'*' * (len(e164) - 6)}{e164[-4:]}"


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


def notify_number_activated(
    db: Session, *, account_id: str, account_email: str, e164: str, organization_name: str
) -> None:
    send_notification(
        db,
        event_name="number.activated",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "e164": e164,
            "number_formatted": e164,
            "organization_name": organization_name,
            "user_display_name": account_email,
        },
    )


def notify_number_order_not_approved(
    db: Session, *, account_id: str, account_email: str, order_reference: str, reason_category: str
) -> None:
    send_notification(
        db,
        event_name="number.order_not_approved",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "order_reference": order_reference,
            "decision_reason_category": reason_category,
        },
    )


def notify_number_assigned(
    db: Session, *, account_id: str, account_email: str, e164: str, organization_name: str
) -> None:
    send_notification(
        db,
        event_name="number.assigned",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "number_formatted": e164,
            "organization_name": organization_name,
            "number_assignment_type": "Direct assignment",
        },
    )


def notify_number_unassigned(
    db: Session,
    *,
    account_id: str,
    account_email: str,
    e164: str,
    previous_target: str,
    lifecycle_status: str,
    route_summary: str,
) -> None:
    send_notification(
        db,
        event_name="number.unassigned",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "number_formatted": e164,
            "assignment_previous_target": previous_target,
            "number_lifecycle_status": lifecycle_status,
            "number_current_route_summary": route_summary,
        },
    )


def notify_number_verification_required(
    db: Session, *, account_id: str, account_email: str, e164: str, action_summary: str
) -> None:
    send_notification(
        db,
        event_name="number.verification_required",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "number_formatted": e164,
            "case_action_summary": action_summary,
        },
    )


def notify_number_released(db: Session, *, account_id: str, account_email: str, e164: str) -> None:
    send_notification(
        db,
        event_name="number.released",
        account_id=account_id,
        recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "number_formatted": e164,
            "release_completed_at": _now_str(),
        },
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


def notify_porting_request_submitted(
    db: Session, *, account_id: str, account_email: str, phone_number: str, port_reference: str
) -> None:
    send_notification(
        db, event_name="porting.submitted", account_id=account_id, recipient_email=account_email,
        context={
            "phone_number": phone_number,
            "user_display_name": account_email,
            "port_reference": port_reference,
            "number_masked_or_formatted": _mask_number(phone_number),
        },
    )


def notify_porting_request_approved(
    db: Session, *, account_id: str, account_email: str, phone_number: str, port_reference: str
) -> None:
    send_notification(
        db, event_name="porting.approved", account_id=account_id, recipient_email=account_email,
        context={
            "phone_number": phone_number,
            "user_display_name": account_email,
            "port_reference": port_reference,
            "number_masked_or_formatted": _mask_number(phone_number),
            "port_submitted_at": _now_str(),
            "port_estimated_completion": "to be confirmed by the Porting Center",
        },
    )


def notify_porting_request_rejected(
    db: Session,
    *,
    account_id: str,
    account_email: str,
    phone_number: str,
    port_reference: str,
    reason: str | None = None,
) -> None:
    send_notification(
        db, event_name="porting.rejected", account_id=account_id, recipient_email=account_email,
        context={
            "phone_number": phone_number,
            "reason_line": f" Reason: {reason}" if reason else "",
            "user_display_name": account_email,
            "port_reference": port_reference,
            "port_rejection_summary": reason or "No reason provided",
        },
    )


def notify_porting_request_completed(
    db: Session, *, account_id: str, account_email: str, phone_number: str, port_reference: str
) -> None:
    send_notification(
        db, event_name="porting.completed", account_id=account_id, recipient_email=account_email,
        context={
            "phone_number": phone_number,
            "user_display_name": account_email,
            "port_reference": port_reference,
            "number_formatted": phone_number,
            "port_completed_at": _now_str(),
        },
    )


def notify_porting_request_canceled(
    db: Session, *, account_id: str, account_email: str, port_reference: str, canceled_by: str
) -> None:
    send_notification(
        db, event_name="porting.canceled", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "port_reference": port_reference,
            "port_canceled_at": _now_str(),
            "port_canceled_by": canceled_by,
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


def notify_password_reset_requested(db: Session, *, account_id: str, user_email: str, token: str) -> None:
    reset_url = f"{settings.frontend_base_url}/reset-password?token={token}"
    send_notification(
        db,
        event_name="auth.password_reset",
        account_id=account_id,
        recipient_email=user_email,
        context={"reset_url": reset_url, "user_display_name": user_email},
    )


def notify_account_activated(db: Session, *, account_id: str, user_email: str) -> None:
    send_notification(
        db, event_name="auth.account_activated", account_id=account_id, recipient_email=user_email,
        context={"user_display_name": user_email},
    )


def notify_password_changed(db: Session, *, account_id: str, user_email: str) -> None:
    send_notification(
        db, event_name="auth.password_changed", account_id=account_id, recipient_email=user_email,
        context={"user_display_name": user_email, "security_activity_time": _now_str()},
    )


def notify_mfa_enabled(db: Session, *, account_id: str, user_email: str) -> None:
    send_notification(
        db, event_name="auth.mfa_enabled", account_id=account_id, recipient_email=user_email,
        context={"user_display_name": user_email, "security_activity_time": _now_str()},
    )


def notify_mfa_disabled(db: Session, *, account_id: str, user_email: str) -> None:
    send_notification(
        db, event_name="auth.mfa_disabled", account_id=account_id, recipient_email=user_email,
        context={"user_display_name": user_email, "security_activity_time": _now_str()},
    )


def notify_emergency_calling_notice(
    db: Session, *, account_id: str, account_email: str, resource_summary: str, capability_status: str
) -> None:
    send_notification(
        db, event_name="compliance.emergency_calling_notice", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "resource_summary": resource_summary,
            "emergency_capability_status": capability_status,
        },
    )


def notify_high_risk_destination_blocked(
    db: Session, *, account_id: str, account_email: str, from_number: str, to_number: str, reason: str
) -> None:
    send_notification(
        db, event_name="voice.high_risk_destination_blocked", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "call_source_masked": _mask_number(from_number),
            "call_destination_masked": _mask_number(to_number),
            "call_started_at": _now_str(),
            "decision_reason_category": reason,
        },
    )


def notify_call_summary_available(
    db: Session, *, account_id: str, account_email: str, counterparty: str
) -> None:
    send_notification(
        db, event_name="voice.call_summary_available", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "call_started_local": _now_str(),
            "call_counterparty_safe": counterparty,
        },
    )


def notify_voicemail_received(
    db: Session, *, account_id: str, account_email: str, e164: str, from_number: str, duration: int | None
) -> None:
    send_notification(
        db, event_name="voice.voicemail_received", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "number_masked_or_formatted": _mask_number(e164),
            "voicemail_received_local": _now_str(),
            "call_caller_display_safe": _mask_number(from_number),
            "voicemail_duration": f"{duration}s" if duration is not None else "unknown",
        },
    )


def notify_voicemail_transcription_ready(db: Session, *, account_id: str, account_email: str) -> None:
    send_notification(
        db, event_name="voice.voicemail_transcription_ready", account_id=account_id, recipient_email=account_email,
        context={"user_display_name": account_email, "voicemail_received_local": _now_str()},
    )


def notify_plan_started(
    db: Session, *, account_id: str, account_email: str, organization_name: str, plan_name: str,
    billing_interval: str, next_billing_date: str,
) -> None:
    send_notification(
        db, event_name="billing.plan_started", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "organization_name": organization_name,
            "subscription_plan_name": plan_name,
            "subscription_billing_interval": billing_interval,
            "subscription_next_billing_date": next_billing_date,
        },
    )


def notify_trial_started(
    db: Session, *, account_id: str, account_email: str, organization_name: str, plan_name: str,
    trial_end_date: str,
) -> None:
    send_notification(
        db, event_name="billing.trial_started", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "organization_name": organization_name,
            "subscription_plan_name": plan_name,
            "subscription_trial_end_date": trial_end_date,
            "subscription_post_trial_terms": "your plan continues on the standard billing terms shown in Billing",
        },
    )


def notify_plan_changed(
    db: Session, *, account_id: str, account_email: str, organization_name: str, previous_plan: str,
    new_plan: str,
) -> None:
    send_notification(
        db, event_name="billing.plan_changed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "organization_name": organization_name,
            "subscription_previous_plan": previous_plan,
            "subscription_plan_name": new_plan,
            "subscription_effective_at": _now_str(),
            "transaction_adjustment_summary": "shown in Billing",
        },
    )


def notify_payment_failed(db: Session, *, account_id: str, account_email: str, plan_name: str) -> None:
    send_notification(
        db, event_name="billing.payment_failed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "transaction_total": "the subscription",
            "transaction_currency": "amount",
            "transaction_description": f"{plan_name} plan services",
            "transaction_failure_category": "payment method issue",
        },
    )


def notify_payment_reminder(
    db: Session, *, account_id: str, account_email: str, plan_name: str, grace_period_ends_at: str
) -> None:
    send_notification(
        db, event_name="billing.payment_reminder", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "transaction_total": "the subscription",
            "transaction_currency": "amount",
            "transaction_description": f"{plan_name} plan services",
            "dunning_suspension_date": grace_period_ends_at,
            "dunning_consequence_summary": (
                "Outbound calling, video, number purchases, and AI features will pause"
            ),
        },
    )


def notify_organization_verification_submitted(
    db: Session, *, account_id: str, account_email: str, organization_name: str, case_reference: str
) -> None:
    send_notification(
        db, event_name="org.verification_submitted", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "organization_name": organization_name,
            "case_reference": case_reference,
        },
    )


def notify_administrator_added(
    db: Session, *, account_id: str, account_email: str, organization_name: str, new_admin_display_name: str,
) -> None:
    send_notification(
        db, event_name="org.administrator_added", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "organization_name": organization_name,
            "actor_display_name": new_admin_display_name,
            "role_name": "Administrator",
            "event_occurred_at": _now_str(),
        },
    )


def notify_administrator_removed(
    db: Session, *, account_id: str, account_email: str, organization_name: str, removed_admin_display_name: str,
) -> None:
    send_notification(
        db, event_name="org.administrator_removed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "organization_name": organization_name,
            "actor_display_name": removed_admin_display_name,
            "role_name": "Administrator",
        },
    )


def notify_service_restored(db: Session, *, account_id: str, account_email: str) -> None:
    send_notification(
        db, event_name="billing.service_restored", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "scope_summary": "Outbound calling, video, number purchases, and AI features",
            "event_occurred_at": _now_str(),
        },
    )
