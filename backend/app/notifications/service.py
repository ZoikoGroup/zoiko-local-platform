import hashlib
import hmac
import base64
import re
import unicodedata
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.config import settings
from app.events.service import publish_notification_sent
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.notifications.email import EmailError, send_email
from app.integrations.notifications.webpush import PushError, PushSubscriptionExpiredError, send_push
from app.integrations.telecom.twilio import TelecomError, send_sms
from app.notifications.models import (
    NotificationCategory,
    NotificationChannel,
    NotificationDelivery,
    NotificationDeliveryStatus,
    NotificationPreference,
    NotificationPriority,
    NotificationSuppression,
    NotificationTemplate,
    PushSubscription,
    SuppressionReason,
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


_SUBJECT_MAX_VALUE_LENGTH = 40
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Zero-width/bidi-control characters used in real spoofing attacks (e.g.
# RLO to visually reverse a suffix) - stripped alongside plain ASCII
# control chars via unicodedata's category check below.
_BIDI_CONTROL_CHARS = "​‌‍\u200E\u200F\u202A\u202B\u202C\u202D\u202E﻿"


def _sanitize_subject_value(value: object) -> object:
    """Email Communications System doc A-17 (BLOCKER) "subject hardening" -
    a customer-controlled string (an org name, a display name, a raw
    webhook URL) landing straight in an email subject line with no
    escaping is both a spoofing vector (bidi/zero-width tricks) and a
    phishing-lookalike vector (an attacker-chosen URL/email address
    appearing to come from this platform's own notification). Only
    applied to the SUBJECT's rendering of context, never the body - the
    body legitimately contains real URLs (e.g. the unsubscribe link) and
    longer text that would break if capped/stripped the same way."""
    if not isinstance(value, str):
        return value
    cleaned = _URL_RE.sub("[link removed]", value)
    cleaned = _EMAIL_RE.sub("[email removed]", cleaned)
    cleaned = "".join(
        ch for ch in cleaned
        if ch not in _BIDI_CONTROL_CHARS and (ch in "\t\n" or unicodedata.category(ch)[0] != "C")
    )
    cleaned = cleaned.strip()
    if len(cleaned) > _SUBJECT_MAX_VALUE_LENGTH:
        cleaned = cleaned[: _SUBJECT_MAX_VALUE_LENGTH - 1].rstrip() + "…"
    return cleaned


def _sanitized_subject_context(context: dict) -> dict:
    return {key: _sanitize_subject_value(value) for key, value in context.items()}


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
    disabled_domains: list[str] | None = None,
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
    if disabled_domains is not None:
        pref.disabled_domains = disabled_domains
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


def check_suppression(
    db: Session, recipient_email: str, domain: str | None, *, is_exempt: bool
) -> NotificationSuppression | None:
    """Email Communications System doc §4.2 "central suppression order" /
    §11.1 "an invalid address is never overridden". A global row
    (domain=None - hard bounce or spam complaint) means the address itself
    is bad and blocks EVERY send regardless of priority, since forcing a
    send to an address that already bounced/complained just repeats the
    same reputation damage. A domain-scoped row (one-click unsubscribe from
    one category) only blocks non-exempt sends - same override SECURITY/
    CRITICAL templates already get over a plain preference opt-out."""
    global_row = (
        db.query(NotificationSuppression)
        .filter(NotificationSuppression.recipient_email == recipient_email, NotificationSuppression.domain.is_(None))
        .first()
    )
    if global_row is not None:
        return global_row
    if is_exempt or domain is None:
        return None
    return (
        db.query(NotificationSuppression)
        .filter(NotificationSuppression.recipient_email == recipient_email, NotificationSuppression.domain == domain)
        .first()
    )


def add_suppression(
    db: Session, *, recipient_email: str, domain: str | None, reason: SuppressionReason
) -> NotificationSuppression:
    existing = (
        db.query(NotificationSuppression)
        .filter(NotificationSuppression.recipient_email == recipient_email, NotificationSuppression.domain == domain)
        .first()
    )
    if existing is not None:
        return existing

    suppression = NotificationSuppression(recipient_email=recipient_email, domain=domain, reason=reason)
    db.add(suppression)
    db.commit()
    db.refresh(suppression)
    log_event(
        db, actor="system:notifications", action="notification.suppression_added",
        target=f"suppression:{suppression.id}",
        after={"recipient_email": recipient_email, "domain": domain, "reason": reason.value},
    )
    return suppression


def list_suppressions(db: Session, recipient_email: str | None = None) -> list[NotificationSuppression]:
    """Staff-facing view of the central suppression list - the doc's
    "Suppression... [is] audited like deliveries" requirement."""
    query = db.query(NotificationSuppression)
    if recipient_email is not None:
        query = query.filter(NotificationSuppression.recipient_email == recipient_email)
    return query.order_by(NotificationSuppression.created_at.desc()).all()


_UNSUBSCRIBE_TOKEN_SCOPE = "notification_unsubscribe"


def _create_unsubscribe_token(recipient_email: str, domain: str | None) -> str:
    """Stateless (no DB row, no expiry) - an old email should still let its
    recipient unsubscribe years later, matching real-world one-click
    unsubscribe behavior. Signed with the same secret as login tokens, but
    scope='notification_unsubscribe' keeps it from ever being accepted as
    an auth token or vice versa (see core.security.create_access_token's
    'scope' parameter for the same pattern)."""
    payload = {"sub": recipient_email, "domain": domain, "scope": _UNSUBSCRIBE_TOKEN_SCOPE}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm="HS256")


def decode_unsubscribe_token(token: str) -> tuple[str, str | None] | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except JWTError:
        return None
    if payload.get("scope") != _UNSUBSCRIBE_TOKEN_SCOPE:
        return None
    return payload["sub"], payload.get("domain")


def unsubscribe_via_token(db: Session, token: str) -> tuple[bool, str]:
    """Powers the one-click unsubscribe link appended to non-essential
    emails (see send_notification's unsubscribe_url). Domain-scoped by
    design (doc §11.1: "changing one category never silently changes
    another") - this only ever adds a suppression for the one domain
    encoded in the token, never account-wide."""
    decoded = decode_unsubscribe_token(token)
    if decoded is None:
        return False, "This unsubscribe link is invalid."
    recipient_email, domain = decoded
    add_suppression(db, recipient_email=recipient_email, domain=domain, reason=SuppressionReason.MANUAL_UNSUBSCRIBE)
    label = f"{domain} emails" if domain else "these emails"
    return True, f"You've been unsubscribed from {label} at {recipient_email}."


def send_notification(
    db: Session,
    *,
    event_name: str,
    recipient_email: str,
    context: dict,
    account_id: str | None = None,
    idempotency_key: str | None = None,
) -> NotificationDelivery:
    """The one pipeline every domain event goes through, instead of each
    call site building its own hardcoded subject/body string: resolve the
    template, render it, send it, and record what happened either way -
    the doc's "domain services publish events; they do not render or send
    email directly" principle, sized for what this platform actually
    sends today.

    idempotency_key (doc A-03, BLOCKER): optional so this doesn't force a
    diff across every existing notify_* call site in one pass - wired into
    the genuinely retry-prone ones (webhook-triggered sends, e.g. payment
    failed/restored) first. Extending it to every call site is
    straightforward but intentionally not done here - same "representative,
    not universal" scoping this codebase already uses elsewhere (e.g. the
    original Kafka event wiring)."""
    if idempotency_key is not None:
        existing = db.query(NotificationDelivery).filter(
            NotificationDelivery.idempotency_key == idempotency_key
        ).first()
        if existing is not None:
            return existing

    template = db.query(NotificationTemplate).filter(NotificationTemplate.key == event_name).first()
    if template is None:
        raise NotificationTemplateMissingError(f"No notification template registered for event {event_name!r}")

    is_exempt = _is_exempt_from_suppression(template)
    subject = template.subject_template.format(**_sanitized_subject_context(context))
    body = template.body_template.format(**context)

    delivery = NotificationDelivery(
        account_id=account_id,
        event_name=event_name,
        recipient_email=recipient_email,
        subject=subject,
        status=NotificationDeliveryStatus.SENT,
    )

    suppression = check_suppression(db, recipient_email, template.domain, is_exempt=is_exempt)

    # A bare account_id=None call (no account context yet, e.g. pre-signup
    # flows) has no preference to check, so it always sends.
    pref = get_or_create_preference(db, account_id) if (account_id is not None and not is_exempt) else None
    is_opted_out = pref is not None and (
        not pref.transactional_enabled
        or (template.domain is not None and template.domain in pref.disabled_domains)
    )
    # Doc §A-29 (HIGH): configurable-category mail respects the account's
    # quiet hours the same way SMS already does - CRITICAL/SECURITY still
    # bypass this, same override every other suppression check gets.
    is_quiet_hours = pref is not None and not is_exempt and is_within_quiet_hours(pref)

    if suppression is not None:
        delivery.status = NotificationDeliveryStatus.SUPPRESSED
        delivery.error = f"Suppressed: {suppression.reason.value} on file for this address"
    elif is_opted_out:
        delivery.status = NotificationDeliveryStatus.SUPPRESSED
    elif is_quiet_hours:
        delivery.status = NotificationDeliveryStatus.SUPPRESSED
        delivery.error = "Suppressed: sent during the account's configured quiet hours"
    else:
        outgoing_body = body
        email_headers = None
        if not is_exempt:
            # RFC 8058 one-click unsubscribe (doc §4.1/§11, A-23 BLOCKER) -
            # both the body link (for clients with no one-click support)
            # AND the List-Unsubscribe/List-Unsubscribe-Post headers Gmail/
            # Yahoo actually require to show their one-click button - a
            # body link alone satisfies neither. Built once in Python
            # rather than requiring every one of the 195 template bodies
            # to embed a {unsubscribe_url} placeholder themselves.
            token = _create_unsubscribe_token(recipient_email, template.domain)
            unsubscribe_url = f"{settings.public_base_url}/notifications/unsubscribe?token={token}"
            label = template.domain or "these"
            outgoing_body = f"{body}\n\n---\nDon't want {label} emails like this? Unsubscribe: {unsubscribe_url}"
            email_headers = {
                "List-Unsubscribe": f"<{unsubscribe_url}>",
                "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            }
        try:
            delivery.provider_message_id = send_email(
                to=recipient_email, subject=subject, body=outgoing_body, headers=email_headers
            )
        except EmailError as e:
            delivery.status = NotificationDeliveryStatus.FAILED
            delivery.error = str(e)
            if is_exempt:
                # OPS-INT-001 "Critical Email Delivery Failure" - this
                # branch only reaches here for SECURITY/CRITICAL templates
                # (the doc's "essential security, porting, emergency,
                # billing, or legal" class), which is exactly the case
                # where silent failure is the worst outcome. Guarded
                # against recursing into itself: send_internal_alert's own
                # send_email failures are swallowed per-recipient inside
                # that function, never raised back here.
                try:
                    # delivery.id isn't populated yet (its UUID default
                    # fires at flush/commit time, both of which happen
                    # after this block) - event_name+recipient is already
                    # a stable, real reference at this point.
                    send_internal_alert(
                        db, event_name="ops_int.critical_email_delivery_failure",
                        summary=(
                            f"Template {event_name!r} failed to send to {recipient_email} after retries: {e}"
                        ),
                        console_link=f"{settings.public_base_url}/staff",
                        delivery_reference=f"{event_name}:{recipient_email}",
                    )
                except NotificationTemplateMissingError:
                    pass

    db.add(delivery)
    db.commit()
    db.refresh(delivery)

    # Webhook and CRM-activity-sync counterparts to email - both live here
    # rather than at each of the ~40 call sites that already call
    # send_notification (see app.webhooks.service.dispatch_webhook_event's
    # docstring). account_id=None calls (pre-signup) have no account to
    # dispatch to.
    if account_id is not None:
        from app.crm.service import sync_activity_to_crm
        from app.webhooks.service import dispatch_webhook_event

        dispatch_webhook_event(db, account_id=account_id, event_type=event_name, payload=context)
        sync_activity_to_crm(
            db, account_id=account_id, event_type=event_name, contact_phone=context.get("e164"),
        )

    log_event(
        db,
        actor="system",
        action=f"notification.{delivery.status.value}",
        target=f"notification_delivery:{delivery.id}",
        account_id=account_id,
        after={"event_name": event_name, "recipient_email": recipient_email, "status": delivery.status},
    )
    publish_notification_sent(account_id, event_name=event_name, channel="email", status=delivery.status.value)

    if account_id:
        _fan_out_push(db, account_id=account_id, event_name=event_name, title=subject, body=body, is_exempt=is_exempt)

    return delivery


def send_internal_alert(db: Session, *, event_name: str, summary: str, console_link: str = "", **extra: str) -> None:
    """Email Communications System doc §10 "Internal Operational Alerts" -
    staff/ops notifications, a different audience and delivery path from
    send_notification above: no suppression, no unsubscribe, no per-
    account preference (staff aren't accounts - PlatformStaff has no FK to
    accounts.id), and no NotificationDelivery row (that table's account_id
    is a required FK to accounts.id). Audited via log_event instead,
    matching every other sensitive/system action in this codebase.

    Delivered to every SUPER_ADMIN - the doc names no distribution-
    list/on-call mechanism, so this uses the same highest-trust role every
    other platform-wide sensitive action here is already gated to. Real
    vendor paging (PagerDuty/Opsgenie) doesn't exist yet; email is the
    only real channel today - the same gap OPS-INT-001 itself names
    ("alternate-channel decision") when email delivery is what's failing.

    `summary`/`console_link` are the two placeholders every one of the 39
    seeded body templates shares (see that migration's docstring on why
    the required-content-category guidance became two generic fields, not
    39 bespoke ones) - `extra` covers whatever the subject line itself
    needs (e.g. case_reference, tenant_reference)."""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.key == event_name).first()
    if template is None:
        raise NotificationTemplateMissingError(f"No internal alert template registered for event {event_name!r}")

    context = {"summary": summary, "console_link": console_link, **extra}
    subject = template.subject_template.format(**context)
    body = template.body_template.format(**context)

    from app.staff.models import PlatformStaff, PlatformStaffRole

    recipients = db.query(PlatformStaff).filter(PlatformStaff.role == PlatformStaffRole.SUPER_ADMIN).all()
    sent_to: list[str] = []
    for staff in recipients:
        try:
            send_email(to=staff.email, subject=subject, body=body)
            sent_to.append(staff.email)
        except EmailError:
            # One staff mailbox bouncing must never block the others, and
            # never propagate into the real event that triggered this
            # alert (e.g. a reconciliation run) - that event already
            # succeeded/failed on its own merits regardless of whether
            # anyone got told about it.
            pass

    log_event(
        db, actor="system", action="internal_alert.sent", target=f"notification_template:{template.id}",
        after={"event_name": event_name, "subject": subject, "recipients": sent_to},
    )


def _fan_out_push(
    db: Session, *, account_id: str, event_name: str, title: str, body: str, is_exempt: bool = False
) -> None:
    """Best-effort push to every device subscribed for this account - a
    missing/expired subscription or a real push-service failure must never
    break the (already-sent) email notification, same rationale as SMS's
    try/except in notify_number_suspended.

    Same opt-out/quiet-hours gating as SMS (transactional_enabled +
    is_within_quiet_hours, bypassed for SECURITY/CRITICAL templates) - push
    is an interruptive channel too (buzzes a phone/desktop), and previously
    had zero preference checking at all: every subscriber got pushed
    regardless of what they'd opted out of."""
    subscriptions = db.query(PushSubscription).filter(PushSubscription.account_id == account_id).all()
    if not subscriptions:
        return

    pref = get_or_create_preference(db, account_id) if not is_exempt else None
    if pref is not None and not pref.transactional_enabled:
        suppressed_reason = "Suppressed: opted out of transactional notifications for this account"
    elif pref is not None and is_within_quiet_hours(pref):
        suppressed_reason = "Suppressed: sent during the account's configured quiet hours"
    else:
        suppressed_reason = None

    for subscription in subscriptions:
        delivery = NotificationDelivery(
            account_id=account_id,
            event_name=event_name,
            channel=NotificationChannel.PUSH,
            push_subscription_id=subscription.id,
            subject=title[:255],
            status=NotificationDeliveryStatus.SENT,
        )
        if suppressed_reason is not None:
            delivery.status = NotificationDeliveryStatus.SUPPRESSED
            delivery.error = suppressed_reason
        else:
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


class WebhookSignatureError(Exception):
    """Raised when a Resend webhook's signature doesn't verify - the
    payload is discarded without being processed."""


def _verify_resend_signature(payload: bytes, svix_id: str, svix_timestamp: str, svix_signature: str) -> bool:
    """Resend signs webhooks the same way as Svix's other webhook products:
    HMAC-SHA256 over "{id}.{timestamp}.{body}" using the base64-decoded
    portion of the whsec_... secret, compared against each space-separated
    "v1,<sig>" entry in the svix-signature header. Always False while
    RESEND_WEBHOOK_SECRET is blank (no webhook endpoint registered yet)."""
    if not settings.resend_webhook_secret:
        return False
    secret = settings.resend_webhook_secret
    secret_bytes = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    signed_content = f"{svix_id}.{svix_timestamp}.{payload.decode('utf-8')}".encode("utf-8")
    expected = base64.b64encode(hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()).decode("utf-8")
    for entry in svix_signature.split(" "):
        if "," not in entry:
            continue
        _version, sig = entry.split(",", 1)
        if hmac.compare_digest(expected, sig):
            return True
    return False


_RESEND_EVENT_TO_DELIVERY_STATUS = {
    "email.delivered": NotificationDeliveryStatus.DELIVERED,
    "email.bounced": NotificationDeliveryStatus.BOUNCED,
    "email.complained": NotificationDeliveryStatus.COMPLAINED,
    "email.clicked": NotificationDeliveryStatus.CLICKED,
}


def handle_resend_webhook(
    db: Session, *, payload: bytes, svix_id: str, svix_timestamp: str, svix_signature: str
) -> None:
    """Updates the delivery ledger from Resend's real bounce/complaint/
    delivered/clicked events, and feeds hard bounces + spam complaints into
    the central suppression list (doc §4.2) - the only way suppression data
    enters the system organically, short of a manual unsubscribe click.
    NOT tested against a live account - no webhook endpoint has been
    registered in the Resend dashboard yet (RESEND_WEBHOOK_SECRET is
    blank), same caveat as this codebase's other real-but-unverified
    integrations. Treats every email.bounced event as suppression-worthy
    rather than trying to distinguish hard/soft bounce sub-types from an
    unverified payload shape - matches the doc's stated bias ("an invalid
    address is never overridden") toward caution over redelivery."""
    if not _verify_resend_signature(payload, svix_id, svix_timestamp, svix_signature):
        raise WebhookSignatureError("Resend webhook signature did not verify")

    import json

    event = json.loads(payload)
    event_type = event.get("type")
    data = event.get("data", {})
    message_id = data.get("email_id")
    recipient_email = (data.get("to") or [None])[0]

    new_status = _RESEND_EVENT_TO_DELIVERY_STATUS.get(event_type)
    if new_status is not None and message_id:
        delivery = (
            db.query(NotificationDelivery)
            .filter(NotificationDelivery.provider_message_id == message_id)
            .first()
        )
        if delivery is not None:
            delivery.status = new_status
            db.commit()

    if recipient_email and event_type == "email.bounced":
        add_suppression(db, recipient_email=recipient_email, domain=None, reason=SuppressionReason.HARD_BOUNCE)
    elif recipient_email and event_type == "email.complained":
        add_suppression(db, recipient_email=recipient_email, domain=None, reason=SuppressionReason.COMPLAINT)


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
    publish_notification_sent(account_id, event_name=event_name, channel="sms", status=delivery.status.value)
    return delivery


def _notifications_cache_key(account_id: str) -> str:
    return f"notifications:list:{account_id}"


# Short TTL, deliberately NOT invalidated on every send_notification call
# site (there are ~40 of them across the whole codebase - see
# send_notification's own docstring) - that would mean touching every
# domain module that ever sends a notification, for a feature the
# dashboard bell already polls periodically rather than needing to be
# instant. A new notification appearing up to this many seconds late is
# an acceptable tradeoff for cutting the DB round-trip on every poll;
# mark_notification_read/mark_all_notifications_read below DO invalidate
# immediately, since those are single, easily-owned write paths and a
# user expects their own "mark read" click to stick right away.
_NOTIFICATIONS_CACHE_TTL_SECONDS = 8


def _serialize_notification(n: NotificationDelivery) -> dict:
    return {
        "id": n.id,
        "account_id": n.account_id,
        "event_name": n.event_name,
        "channel": n.channel.value,
        "recipient_email": n.recipient_email,
        "recipient_phone": n.recipient_phone,
        "push_subscription_id": n.push_subscription_id,
        "subject": n.subject,
        "status": n.status.value,
        "error": n.error,
        "provider_message_id": n.provider_message_id,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


def _deserialize_notification(data: dict) -> NotificationDelivery:
    return NotificationDelivery(
        id=data["id"],
        account_id=data["account_id"],
        event_name=data["event_name"],
        channel=NotificationChannel(data["channel"]),
        recipient_email=data["recipient_email"],
        recipient_phone=data["recipient_phone"],
        push_subscription_id=data["push_subscription_id"],
        subject=data["subject"],
        status=NotificationDeliveryStatus(data["status"]),
        error=data["error"],
        provider_message_id=data["provider_message_id"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
        read_at=datetime.fromisoformat(data["read_at"]) if data["read_at"] else None,
    )


def list_account_notifications(db: Session, account_id: str) -> list[NotificationDelivery]:
    cache_key = _notifications_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_notification(row) for row in cached]
    deliveries = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.desc())
        .all()
    )
    cache_set(
        cache_key, [_serialize_notification(d) for d in deliveries],
        ttl_seconds=_NOTIFICATIONS_CACHE_TTL_SECONDS,
    )
    return deliveries


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
        cache_delete(_notifications_cache_key(account_id))
        log_event(
            db, actor_id=account_id, action="notification.read",
            target_type="notification_delivery", target_id=delivery.id,
        )
    return delivery


def mark_all_notifications_read(db: Session, account_id: str) -> int:
    result = (
        db.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id, NotificationDelivery.read_at.is_(None))
        .update({NotificationDelivery.read_at: datetime.now(timezone.utc)}, synchronize_session=False)
    )
    db.commit()

    if result:
        cache_delete(_notifications_cache_key(account_id))
        log_event(
            db, actor_id=account_id, action="notification.read_all",
            target_type="account", target_id=account_id, metadata={"count": result},
        )
    return result


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


def notify_video_guest_waiting(
    db: Session, *, account_id: str, host_email: str, room_name: str, guest_display_name: str
) -> None:
    """A guest requesting to join a video call (app.media.service.
    request_guest_join) only shows up in the host's waiting-room list if
    they're actively watching the call screen - this is the out-of-band
    alert so a host who's stepped away still finds out, instead of a guest
    waiting the full WAITING_ROOM_TIMEOUT_MINUTES for nothing."""
    send_notification(
        db, event_name="video.guest_waiting", account_id=account_id, recipient_email=host_email,
        context={
            "user_display_name": host_email,
            "video_guest_display_name": guest_display_name,
            "video_room_name": room_name,
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


def notify_receptionist_callback_requested(
    db: Session, *, account_id: str, account_email: str, caller_number: str, callback_window: str
) -> None:
    send_notification(
        db, event_name="voice.receptionist_callback_requested", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "caller_number": _mask_number(caller_number),
            "callback_window": callback_window,
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


def notify_payment_failed(
    db: Session, *, account_id: str, account_email: str, plan_name: str, idempotency_key: str | None = None
) -> None:
    send_notification(
        db, event_name="billing.payment_failed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "transaction_total": "the subscription",
            "transaction_currency": "amount",
            "transaction_description": f"{plan_name} plan services",
            "transaction_failure_category": "payment method issue",
        },
        idempotency_key=idempotency_key,
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


def notify_invoice_available(
    db: Session, *, account_id: str, account_email: str, invoice_reference: str, billing_period: str,
    subtotal: str, tax: str, total: str, currency: str,
) -> None:
    """The 'billing.invoice_available' template was seeded but never
    called from anywhere - run_billing_cycle issued real invoices with no
    customer-facing evidence that one existed. This is the receipt/invoice
    leg of the Production Readiness acceptance chain (Table 22)."""
    send_notification(
        db, event_name="billing.invoice_available", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "transaction_reference": invoice_reference,
            "billing_period": billing_period,
            "transaction_subtotal": subtotal,
            "transaction_tax": tax,
            "transaction_total": total,
            "transaction_currency": currency,
        },
    )


def notify_payment_succeeded(
    db: Session, *, account_id: str, account_email: str, total: str, currency: str, description: str,
    payment_date: str, payment_method_masked: str,
) -> None:
    """Same gap as notify_invoice_available above - 'billing.payment_succeeded'
    was seeded but run_billing_cycle's successful capture never triggered it."""
    send_notification(
        db, event_name="billing.payment_succeeded", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "transaction_total": total,
            "transaction_currency": currency,
            "transaction_description": description,
            "transaction_date": payment_date,
            "transaction_payment_method_masked": payment_method_masked,
        },
    )


def notify_credit_or_refund_processed(
    db: Session, *, account_id: str, account_email: str, adjustment_type: str, amount: str, currency: str,
    reference: str, reason: str,
) -> None:
    """'billing.credit_or_refund_processed' was seeded but no refund path
    (the automatic post-payment-fulfillment-failure refund in
    complete_number_purchase_from_checkout, or the staff-triggered
    refund_zoikonex_payment/credit-note actions) ever called it - a
    customer whose payment was refunded had no way to know it happened."""
    send_notification(
        db, event_name="billing.credit_or_refund_processed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "transaction_adjustment_type": adjustment_type,
            "transaction_adjustment_amount": amount,
            "transaction_currency": currency,
            "transaction_reference": reference,
            "transaction_adjustment_reason": reason,
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


def notify_subscription_terminated(db: Session, *, account_id: str, account_email: str, plan_name: str) -> None:
    """Email Communications System doc's billing.subscription_ended
    template, previously seeded but never wired to a real call site - see
    app.billing.service.terminate_subscription."""
    send_notification(
        db, event_name="billing.subscription_ended", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "subscription_plan_name": plan_name,
            "subscription_ended_date": _now_str(),
            "subscription_post_end_status": "Terminated - owned numbers have been released",
        },
    )


def notify_service_restored(
    db: Session, *, account_id: str, account_email: str, idempotency_key: str | None = None
) -> None:
    send_notification(
        db, event_name="billing.service_restored", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "scope_summary": "Outbound calling, video, number purchases, and AI features",
            "event_occurred_at": _now_str(),
        },
        idempotency_key=idempotency_key,
    )


def notify_api_client_created(
    db: Session, *, account_id: str, account_email: str, label: str, actor_display_name: str
) -> None:
    send_notification(
        db, event_name="intg.api_client_created", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "integration_name": label,
            "environment": "live",
            "actor_display_name": actor_display_name,
            "integration_scope_summary": "read/write access to your account's public API",
        },
    )


def notify_webhook_endpoint_added(
    db: Session, *, account_id: str, account_email: str, url: str, actor_display_name: str
) -> None:
    send_notification(
        db, event_name="intg.webhook_endpoint_added", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "integration_name": url,
            "change_action": "added",
            "actor_display_name": actor_display_name,
            "integration_destination_safe": url,
            "integration_event_summary": "all account events",
            "integration_signing_status": "HMAC-SHA256 signed",
        },
    )


def notify_integration_installed(
    db: Session, *, account_id: str, account_email: str, integration_name: str, organization_name: str,
    actor_display_name: str,
) -> None:
    send_notification(
        db, event_name="intg.integration_installed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "integration_name": integration_name,
            "organization_name": organization_name,
            "actor_display_name": actor_display_name,
            "integration_scope_summary": "contact and call activity sync",
        },
    )


def notify_integration_removed(
    db: Session, *, account_id: str, account_email: str, integration_name: str, organization_name: str,
) -> None:
    send_notification(
        db, event_name="intg.integration_removed", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "integration_name": integration_name,
            "organization_name": organization_name,
            "event_occurred_at": _now_str(),
        },
    )


def notify_call_flow_published(
    db: Session, *, account_id: str, account_email: str, flow_name: str, number_summary: str, actor_display_name: str,
) -> None:
    send_notification(
        db, event_name="route.call_flow_published", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "route_name": flow_name,
            "route_version": "1",
            "route_number_summary": number_summary,
            "actor_display_name": actor_display_name,
            "route_effective_at": _now_str(),
        },
    )


def notify_call_flow_rolled_back(
    db: Session, *, account_id: str, account_email: str, flow_name: str, restored_version: int,
) -> None:
    send_notification(
        db, event_name="route.call_flow_rollback", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "route_name": flow_name,
            "route_version": str(restored_version),
            "event_occurred_at": _now_str(),
            "route_rollback_reason": "requested by an account admin",
        },
    )


def notify_recipient_opted_out(
    db: Session, *, account_id: str, account_email: str, destination_masked: str, sender_summary: str,
) -> None:
    send_notification(
        db, event_name="msg.recipient_opted_out", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "message_destination_masked": destination_masked,
            "messaging_sender_summary": sender_summary,
            "event_occurred_at": _now_str(),
        },
    )


def notify_account_warning(
    db: Session, *, account_id: str, account_email: str, policy_area: str, case_reference: str
) -> None:
    send_notification(
        db, event_name="trust.account_warning", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "scope_summary": "your account",
            "decision_policy_area": policy_area,
            "case_reference": case_reference,
        },
    )


def notify_account_suspended_for_risk(
    db: Session, *, account_id: str, account_email: str, reason_category: str, case_reference: str
) -> None:
    send_notification(
        db, event_name="trust.account_suspended_or_disabled", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "restriction_status": "suspended",
            "restriction_start_at": _now_str(),
            "decision_reason_category": reason_category,
            "case_reference": case_reference,
        },
    )


def notify_incident_declared(
    db: Session, *, account_id: str, account_email: str, affected_service: str, impact_summary: str
) -> None:
    send_notification(
        db, event_name="ops.service_incident_declared", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "incident_affected_service": affected_service,
            "incident_started_local": _now_str(),
            "incident_impact_summary": impact_summary,
            "incident_status": "investigating",
        },
    )


def notify_incident_update(
    db: Session, *, account_id: str, account_email: str, incident_reference: str, status: str,
    impact_summary: str, mitigation_summary: str | None,
) -> None:
    send_notification(
        db, event_name="ops.incident_update", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "incident_reference": incident_reference,
            "incident_status": status,
            "incident_impact_summary": impact_summary,
            "incident_mitigation_summary": mitigation_summary or "Update in progress.",
            "incident_next_update_at": "as soon as there's a material change",
        },
    )


def notify_incident_resolved(
    db: Session, *, account_id: str, account_email: str, incident_reference: str, duration_summary: str
) -> None:
    send_notification(
        db, event_name="ops.incident_resolved", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "incident_reference": incident_reference,
            "incident_resolved_local": _now_str(),
            "incident_duration_summary": duration_summary,
        },
    )


def notify_status_subscription_confirmed(db: Session, *, account_id: str, account_email: str) -> None:
    send_notification(
        db, event_name="ops.status_subscription_confirmation", account_id=account_id, recipient_email=account_email,
        context={
            "user_display_name": account_email,
            "status_subscription_summary": "all Zoiko Local service incidents",
        },
    )
