from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.models import Plan, Subscription, SubscriptionStatus

DEFAULT_PLAN_CODE = "free_trial"
_PERIOD_LENGTH = timedelta(days=30)


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

    log_event(
        db, actor=actor, action="subscription.plan_changed", target=f"subscription:{sub.id}",
        before={"plan_code": before_plan}, after={"plan_code": sub.plan_code},
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
