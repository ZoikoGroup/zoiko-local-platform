from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.models import Plan, Subscription, SubscriptionStatus, ZoikoNexSyncEvent, ZoikoNexSyncEventType
from app.integrations.billing import zoikonex as zoikonex_adapter
from app.notifications.service import (
    notify_payment_failed,
    notify_payment_reminder,
    notify_plan_changed,
    notify_plan_started,
    notify_service_restored,
    notify_trial_started,
)

DEFAULT_PLAN_CODE = "free_trial"
_PERIOD_LENGTH = timedelta(days=30)
# Architecture doc §9 "Graceful degradation" - no specific number is given
# in the spec, so this is a reasonable Phase-1 default, stored as a
# constant (not per-plan) since the doc describes it as a platform-wide
# policy, not a plan feature.
GRACE_PERIOD_DAYS = 7


def _db_now(db: Session) -> datetime:
    """Postgres's now() is frozen to the enclosing transaction's start time,
    not a fresh clock read per statement - using it (rather than Python's
    datetime.now()) to stamp a new billing period keeps it consistent with
    UsageEvent.created_at (server_default=func.now()), so a usage event
    recorded moments after a subscription is created in the same
    transaction is never incorrectly excluded from that period's summary by
    a period_start that raced ahead of it."""
    return db.execute(sa.select(sa.func.now())).scalar()


class PlanNotFoundError(Exception):
    """Raised when a plan_code doesn't match any seeded Plan row."""


class NumberQuotaExceededError(Exception):
    """Raised when purchasing another number would exceed the account's
    plan's max_numbers - a Phase-1-local entitlement gate (Architecture
    doc §5's "Subscription and Entitlement" service), independent of
    ZoikoNex, which doesn't exist yet."""


class SeatQuotaExceededError(Exception):
    """Raised when adding another team member would exceed the account's
    plan's max_team_seats."""


def list_plans(db: Session) -> list[Plan]:
    return db.query(Plan).order_by(Plan.sort_order).all()


def get_plan(db: Session, plan_code: str) -> Plan:
    plan = db.query(Plan).filter(Plan.plan_code == plan_code).first()
    if plan is None:
        raise PlanNotFoundError(f"No such plan: {plan_code!r}")
    return plan


def _new_period(now: datetime) -> tuple[datetime, datetime]:
    return now, now + _PERIOD_LENGTH


def sync_subscription_to_zoikonex(db: Session, sub: Subscription) -> Subscription:
    """Architecture doc §9 "Subscription sync". Best-effort in spirit (the
    real adapter will eventually need retry/dead-letter handling), but
    since this is a mock with no way to fail, it always succeeds - the
    seam is what matters, not fault-tolerance for a call that never
    actually goes over the network yet."""
    result = zoikonex_adapter.sync_subscription(
        subscription_id=sub.id, account_id=sub.account_id, plan_code=sub.plan_code, status=sub.status.value,
    )
    sub.zoikonex_ref = result["zoikonex_ref"]
    db.add(
        ZoikoNexSyncEvent(
            account_id=sub.account_id,
            event_type=ZoikoNexSyncEventType.SUBSCRIPTION_SYNC,
            zoikonex_ref=result["zoikonex_ref"],
            payload={"subscription_id": sub.id, "plan_code": sub.plan_code, "status": sub.status.value},
        )
    )
    db.commit()
    db.refresh(sub)
    return sub


def sync_usage_event_to_zoikonex(db: Session, usage_event) -> None:
    """Architecture doc §9 "Usage sync". Takes the already-committed
    UsageEvent (see app.usage.service.record_usage_event) and mirrors it
    into the sync ledger - a separate write, not part of the same
    transaction, since usage capture must never fail or roll back because
    a downstream billing sync (mock or real) had a problem.

    Also asks ZoikoNex to rate the event and applies the result to
    usage_event.estimated_cost_cents - the actual $ decision must come
    from here, not be pre-computed by the caller (see
    zoikonex_adapter.rate_usage_event's docstring)."""
    rating = zoikonex_adapter.rate_usage_event(
        db, event_type=usage_event.event_type, quantity=float(usage_event.quantity),
        unit=usage_event.unit, country_band=usage_event.country_band,
    )
    usage_event.estimated_cost_cents = rating["estimated_cost_cents"]

    result = zoikonex_adapter.sync_usage_event(
        usage_event_id=usage_event.id, account_id=usage_event.account_id,
        event_type=usage_event.event_type, quantity=float(usage_event.quantity), unit=usage_event.unit,
    )
    db.add(
        ZoikoNexSyncEvent(
            account_id=usage_event.account_id,
            event_type=ZoikoNexSyncEventType.USAGE_SYNC,
            zoikonex_ref=result["zoikonex_ref"],
            payload={
                "usage_event_id": usage_event.id, "event_type": usage_event.event_type,
                "quantity": float(usage_event.quantity), "unit": usage_event.unit,
                "estimated_cost_cents": rating["estimated_cost_cents"],
            },
        )
    )
    db.commit()


def get_or_create_subscription(db: Session, account_id: str) -> Subscription:
    """Created lazily on first access (same pattern as
    NotificationPreference), defaulting to the free trial plan - every
    account gets one without needing a migration backfill or a second
    code path in signup/Google auth. Also rolls the billing period forward
    (and trial -> active) if the current period has lapsed - there's no
    real billing cycle engine yet, so this is evaluated lazily rather than
    via a scheduled job."""
    sub = db.query(Subscription).filter(Subscription.account_id == account_id).first()
    now = _db_now(db)

    if sub is None:
        plan = get_plan(db, DEFAULT_PLAN_CODE)
        period_start, period_end = _new_period(now)
        sub = Subscription(
            account_id=account_id,
            plan_code=plan.plan_code,
            status=SubscriptionStatus.TRIALING if plan.trial_days > 0 else SubscriptionStatus.ACTIVE,
            trial_ends_at=now + timedelta(days=plan.trial_days) if plan.trial_days > 0 else None,
            current_period_start=period_start,
            current_period_end=period_end,
        )
        db.add(sub)
        db.commit()
        db.refresh(sub)
        sub = sync_subscription_to_zoikonex(db, sub)

        from app.numbering.identity.models import Account, User, UserRole

        owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
        if owner is not None:
            account = db.query(Account).filter(Account.id == account_id).first()
            organization_name = account.name if account else "your organization"
            if plan.trial_days > 0:
                notify_trial_started(
                    db, account_id=account_id, account_email=owner.email, organization_name=organization_name,
                    plan_name=plan.name, trial_end_date=sub.trial_ends_at.strftime("%Y-%m-%d") if sub.trial_ends_at else "",
                )
            else:
                notify_plan_started(
                    db, account_id=account_id, account_email=owner.email, organization_name=organization_name,
                    plan_name=plan.name, billing_interval="monthly",
                    next_billing_date=sub.current_period_end.strftime("%Y-%m-%d"),
                )
        return sub

    changed = False
    if sub.current_period_end < now:
        sub.current_period_start, sub.current_period_end = _new_period(now)
        changed = True
    if sub.status == SubscriptionStatus.TRIALING and sub.trial_ends_at is not None and sub.trial_ends_at < now:
        # No payment processor exists to actually charge anyone yet (see
        # Subscription's docstring) - the honest Phase-1 behavior is to
        # keep the account working past trial end, not silently lock it
        # out with no way to pay. ZoikoNex sync will replace this once built.
        sub.status = SubscriptionStatus.ACTIVE
        changed = True
    if changed:
        db.commit()
        db.refresh(sub)
    return sub


def change_plan(db: Session, account_id: str, plan_code: str, *, actor: str) -> Subscription:
    plan = get_plan(db, plan_code)  # raises PlanNotFoundError for an invalid code
    sub = get_or_create_subscription(db, account_id)
    before_plan = sub.plan_code

    sub.plan_code = plan.plan_code
    if sub.status == SubscriptionStatus.TRIALING:
        # Deliberately choosing a plan ends the trial early - matches how
        # every real subscription product treats an explicit upgrade.
        sub.status = SubscriptionStatus.ACTIVE
        sub.trial_ends_at = None
    db.commit()
    db.refresh(sub)
    sync_subscription_to_zoikonex(db, sub)

    log_event(
        db, actor=actor, action="subscription.plan_changed", target=f"subscription:{sub.id}",
        before={"plan_code": before_plan}, after={"plan_code": sub.plan_code},
    )

    from app.numbering.identity.models import Account, User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        before_plan_obj = db.query(Plan).filter(Plan.plan_code == before_plan).first()
        notify_plan_changed(
            db, account_id=account_id, account_email=owner.email,
            organization_name=account.name if account else "your organization",
            previous_plan=before_plan_obj.name if before_plan_obj else before_plan,
            new_plan=plan.name,
        )
    return sub


def assert_number_quota_available(db: Session, account_id: str, *, exclude_number_id: str | None = None) -> None:
    """exclude_number_id excludes the number currently being (re-)purchased
    from its own count - a retry of a number already sitting in
    COMPLIANCE_PENDING for this same account (e.g. after a compliance case
    gets approved) isn't an ADDITIONAL number, so it must not count against
    the quota a second time and block its own retry."""
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)
    query = db.query(PhoneNumber).filter(
        PhoneNumber.account_id == account_id,
        PhoneNumber.status.in_([
            PhoneNumberStatus.PURCHASE_PENDING,
            PhoneNumberStatus.COMPLIANCE_PENDING,
            PhoneNumberStatus.PROVISIONING,
            PhoneNumberStatus.ACTIVE,
            PhoneNumberStatus.SUSPENDED,
        ]),
    )
    if exclude_number_id is not None:
        query = query.filter(PhoneNumber.id != exclude_number_id)
    owned_or_in_flight = query.count()
    if owned_or_in_flight >= plan.max_numbers:
        raise NumberQuotaExceededError(
            f"Your {plan.name} plan allows up to {plan.max_numbers} number(s) - "
            f"upgrade your plan to purchase another."
        )


def assert_seat_quota_available(db: Session, account_id: str) -> None:
    from app.numbering.identity.models import User

    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)
    seat_count = db.query(User).filter(User.account_id == account_id).count()
    if seat_count >= plan.max_team_seats:
        raise SeatQuotaExceededError(
            f"Your {plan.name} plan allows up to {plan.max_team_seats} team seat(s) - "
            f"upgrade your plan to add another member."
        )


class InvalidPaymentEventError(Exception):
    """Raised for an unrecognized simulated payment event type."""


class ZoikoNexRefNotFoundError(Exception):
    """Raised when an inbound ZoikoNex webhook references a zoikonex_ref
    that doesn't match any local Subscription."""


class BillingSuspendedError(Exception):
    """Raised when an account's grace period has expired with unresolved
    PAST_DUE status - Architecture doc §9 "Graceful degradation": outbound
    calling, video, new purchases, and AI features may pause after dunning
    thresholds. Deliberately never raised for inbound calls or existing
    number ownership - see assert_billing_not_suspended's docstring."""


_PAYMENT_EVENT_TYPES = {"payment_failed", "payment_retry", "payment_restored"}


def _apply_zoikonex_payment_event(
    db: Session,
    sub: Subscription,
    event_type: str,
    *,
    actor: str,
    action: str,
    external_event_id: str | None = None,
) -> Subscription:
    """Shared state transition for an inbound ZoikoNex payment event
    (Architecture doc §9: "ZoikoNex sends payment success, failure, retry,
    grace-period, suspension, and restoration events back to Zoiko Local"),
    used by both the staff-triggered simulator and the real webhook."""
    if event_type not in _PAYMENT_EVENT_TYPES:
        raise InvalidPaymentEventError(f"Unknown payment event type: {event_type!r}")

    account_id = sub.account_id
    now = _db_now(db)
    before_status = sub.status

    if event_type == "payment_failed":
        sub.status = SubscriptionStatus.PAST_DUE
        sub.grace_period_ends_at = now + timedelta(days=GRACE_PERIOD_DAYS)
    elif event_type == "payment_restored":
        sub.status = SubscriptionStatus.ACTIVE
        sub.grace_period_ends_at = None
    # payment_retry intentionally changes nothing but is still logged below -
    # it's evidence ZoikoNex is still trying, not a state transition itself.

    db.commit()
    db.refresh(sub)

    db.add(
        ZoikoNexSyncEvent(
            account_id=account_id,
            event_type=ZoikoNexSyncEventType.PAYMENT_EVENT_RECEIVED,
            zoikonex_ref=sub.zoikonex_ref,
            external_event_id=external_event_id,
            payload={"event_type": event_type, "grace_period_ends_at": sub.grace_period_ends_at.isoformat() if sub.grace_period_ends_at else None},
        )
    )
    db.commit()

    log_event(
        db, actor=actor, action=action, target=f"subscription:{sub.id}",
        before={"status": before_status.value}, after={"status": sub.status.value, "event_type": event_type},
    )

    from app.numbering.identity.models import User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        plan = get_plan(db, sub.plan_code)
        if event_type == "payment_failed":
            notify_payment_failed(db, account_id=account_id, account_email=owner.email, plan_name=plan.name)
        elif event_type == "payment_retry":
            notify_payment_reminder(
                db, account_id=account_id, account_email=owner.email, plan_name=plan.name,
                grace_period_ends_at=sub.grace_period_ends_at.strftime("%Y-%m-%d") if sub.grace_period_ends_at else "",
            )
        elif event_type == "payment_restored":
            notify_service_restored(db, account_id=account_id, account_email=owner.email)

    return sub


def simulate_zoikonex_payment_event(db: Session, account_id: str, event_type: str, *, actor: str) -> Subscription:
    """Staff-triggered stand-in (see routes.py) for the real webhook below,
    kept around since there's no real ZoikoNex sending these yet for most
    environments - see app.integrations.billing.zoikonex's docstring."""
    sub = get_or_create_subscription(db, account_id)
    return _apply_zoikonex_payment_event(
        db, sub, event_type, actor=actor, action="subscription.payment_event_simulated"
    )


def handle_zoikonex_payment_webhook(
    db: Session, *, zoikonex_ref: str, event_type: str, external_event_id: str | None
) -> Subscription:
    """Real inbound ZoikoNex -> Zoiko Local payment-state webhook. Looks the
    subscription up by zoikonex_ref (ZoikoNex has no notion of our internal
    account_id) and, if external_event_id was provided, skips re-applying an
    event already recorded - the Architecture doc §9 requires this
    integration be "idempotent" since webhook deliveries can be retried."""
    sub = db.query(Subscription).filter(Subscription.zoikonex_ref == zoikonex_ref).first()
    if sub is None:
        raise ZoikoNexRefNotFoundError(f"No subscription found for zoikonex_ref={zoikonex_ref!r}")

    if external_event_id is not None:
        already_applied = (
            db.query(ZoikoNexSyncEvent).filter(ZoikoNexSyncEvent.external_event_id == external_event_id).first()
        )
        if already_applied is not None:
            return sub

    return _apply_zoikonex_payment_event(
        db, sub, event_type, actor="zoikonex_webhook",
        action="subscription.payment_event_received", external_event_id=external_event_id,
    )


def assert_billing_not_suspended(db: Session, account_id: str) -> None:
    """Gates outbound calling, video room creation, number purchases, and AI
    summary generation - deliberately NOT called for inbound calls or
    anything that would strand an existing number, per the graceful-
    degradation policy's explicit carve-out (Architecture doc §9)."""
    sub = get_or_create_subscription(db, account_id)
    if sub.status != SubscriptionStatus.PAST_DUE or sub.grace_period_ends_at is None:
        return
    now = _db_now(db)
    if now > sub.grace_period_ends_at:
        raise BillingSuspendedError(
            "This account's payment is past due and its grace period has ended - "
            "resolve billing to resume outbound calling, video, purchases, and AI features."
        )


def list_zoikonex_sync_events(db: Session, *, account_id: str | None = None, limit: int = 200) -> list[ZoikoNexSyncEvent]:
    query = db.query(ZoikoNexSyncEvent)
    if account_id:
        query = query.filter(ZoikoNexSyncEvent.account_id == account_id)
    return query.order_by(ZoikoNexSyncEvent.created_at.desc()).limit(limit).all()


def get_zoikonex_reconciliation_summary(db: Session) -> dict:
    """Architecture doc §9 "Reconciliation: daily reconciliation jobs
    compare Zoiko Local entitlements and usage events with ZoikoNex
    invoices, payments, and ledger state. Exceptions must enter an
    operations queue." Even mocked, this is a real check: every
    Subscription should have a zoikonex_ref, and every UsageEvent should
    have a matching sync ledger row - if either count is ever off, that's
    a genuine bug in the sync wiring, exactly what this is meant to catch
    once a real ZoikoNex exists."""
    from app.usage.models import UsageEvent

    total_subscriptions = db.query(Subscription).count()
    synced_subscriptions = db.query(Subscription).filter(Subscription.zoikonex_ref.isnot(None)).count()
    total_usage_events = db.query(UsageEvent).count()
    synced_usage_events = (
        db.query(ZoikoNexSyncEvent).filter(ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.USAGE_SYNC).count()
    )
    return {
        "total_subscriptions": total_subscriptions,
        "synced_subscriptions": synced_subscriptions,
        "unsynced_subscriptions": total_subscriptions - synced_subscriptions,
        "total_usage_events": total_usage_events,
        "synced_usage_events": synced_usage_events,
        "unsynced_usage_events": total_usage_events - synced_usage_events,
    }


def get_usage_summary(db: Session, account_id: str) -> dict:
    """Compares this billing period's real usage against the account's plan
    limits. Voice minutes come from real UsageEvent rows (call_seconds,
    populated since day one of usage metering); video minutes and AI
    summaries are also real UsageEvent rows as of this feature (see
    app.media.service / app.intelligence.service call sites) - informational
    only, not hard-enforced (unlike number/seat counts), since blocking an
    in-progress call or AI generation on a usage cap is a materially bigger
    and riskier change than gating a discrete purchase/invite action."""
    from app.usage.models import UsageEvent
    from sqlalchemy import func as sa_func

    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)

    totals = dict(
        db.query(UsageEvent.event_type, sa_func.coalesce(sa_func.sum(UsageEvent.quantity), 0))
        .filter(UsageEvent.account_id == account_id, UsageEvent.created_at >= sub.current_period_start)
        .group_by(UsageEvent.event_type)
        .all()
    )

    voice_minutes_used = float(totals.get("call_seconds", 0)) / 60
    video_minutes_used = float(totals.get("video_participant_minutes", 0))
    ai_summaries_used = float(totals.get("ai_summary", 0))

    return {
        "plan_code": plan.plan_code,
        "plan_name": plan.name,
        "status": sub.status.value,
        "trial_ends_at": sub.trial_ends_at,
        "current_period_start": sub.current_period_start,
        "current_period_end": sub.current_period_end,
        "resources": [
            {"resource": "voice_minutes", "used": round(voice_minutes_used, 1), "limit": plan.monthly_voice_minutes},
            {"resource": "video_minutes", "used": round(video_minutes_used, 1), "limit": plan.monthly_video_minutes},
            {"resource": "ai_summaries", "used": int(ai_summaries_used), "limit": plan.monthly_ai_summaries},
        ],
    }
