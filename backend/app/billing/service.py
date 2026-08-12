import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.models import (
    BillingActionRequest,
    BillingActionRequestStatus,
    BillingActionType,
    CatalogEntryStatus,
    Plan,
    PriceCatalogEntry,
    Subscription,
    SubscriptionStatus,
    ZoikoNexReconciliationException,
    ZoikoNexReconciliationExceptionType,
    ZoikoNexReconciliationRun,
    ZoikoNexSyncEvent,
    ZoikoNexSyncEventType,
)
from app.core.config import settings
from app.integrations.billing import zoikonex as zoikonex_adapter
from app.integrations.telecom import twilio as telecom
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active
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
# Commercial Billing Operating Standard P0-8 "late-event policy" - how long
# after a call completes its usage event can still land before
# run_zoikonex_reconciliation flags it. Deliberately generous (this is
# "did this miss a billing cycle," not a real-time metering SLA) - no
# specific number is given in the doc, same "reasonable Phase-1 default,
# not invented precision" posture as GRACE_PERIOD_DAYS above.
LATE_EVENT_THRESHOLD = timedelta(hours=24)


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


class PriceCatalogEntryExistsError(Exception):
    """Raised when trying to create a catalog_version that already exists
    for a plan - PriceCatalogEntry is Class A once created (see its
    docstring): a price correction is always a NEW version, never an edit."""


class CannotApprovePlaceholderError(Exception):
    """Raised when trying to approve a catalog entry still marked
    is_placeholder - a placeholder must be replaced by a real entry
    (create_price_catalog_entry with is_placeholder=False) before it can
    ever move to APPROVED. Approving fake test data would be indistinguishable
    from a real commercial sign-off in the audit trail."""


class PriceCatalogEntryNotFoundError(Exception):
    """Raised when approving a price catalog entry id that doesn't exist."""


def get_active_price_catalog_entry(db: Session, plan_code: str) -> PriceCatalogEntry | None:
    """The "current" price for a plan - the most recently created catalog
    entry, whether DRAFT or APPROVED. There is deliberately no query-time
    distinction between the two here: run_billing_cycle is what decides
    whether a DRAFT/placeholder entry is actually chargeable in the
    current environment (Commercial Billing Operating Standard P0-1)."""
    return (
        db.query(PriceCatalogEntry)
        .filter(PriceCatalogEntry.plan_code == plan_code)
        .order_by(PriceCatalogEntry.created_at.desc())
        .first()
    )


def create_price_catalog_entry(
    db: Session, *, plan_code: str, catalog_version: str, amount_minor_units: int,
    currency_code: str = "USD", is_placeholder: bool = True, actor: str,
) -> PriceCatalogEntry:
    """SUPER_ADMIN-gated at the route - locking/changing a price is a
    commercial decision, the same bar as a calling-rate change
    (app.usage.service.upsert_calling_rate). Always creates a NEW row;
    never edits an existing catalog_version (Class A - see
    PriceCatalogEntry's docstring)."""
    existing = (
        db.query(PriceCatalogEntry)
        .filter(PriceCatalogEntry.plan_code == plan_code, PriceCatalogEntry.catalog_version == catalog_version)
        .first()
    )
    if existing is not None:
        raise PriceCatalogEntryExistsError(
            f"Catalog version {catalog_version!r} already exists for plan {plan_code!r} - use a new version"
        )
    entry = PriceCatalogEntry(
        plan_code=plan_code, catalog_version=catalog_version, amount_minor_units=amount_minor_units,
        currency_code=currency_code, is_placeholder=is_placeholder,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    log_event(
        db, actor=actor, action="billing.price_catalog_entry_created", target=f"plan:{plan_code}",
        after={
            "catalog_version": catalog_version, "amount_minor_units": amount_minor_units,
            "currency_code": currency_code, "is_placeholder": is_placeholder,
        },
    )
    return entry


def approve_price_catalog_entry(db: Session, entry_id: str, *, actor: str) -> PriceCatalogEntry:
    """SUPER_ADMIN-gated at the route. Refuses to approve a placeholder
    entry (see CannotApprovePlaceholderError) - a real price decision must
    create its own real (is_placeholder=False) entry first."""
    entry = db.query(PriceCatalogEntry).filter(PriceCatalogEntry.id == entry_id).first()
    if entry is None:
        raise PriceCatalogEntryNotFoundError(f"No such price catalog entry: {entry_id!r}")
    if entry.is_placeholder:
        raise CannotApprovePlaceholderError(
            f"Catalog entry {entry_id!r} is a placeholder/test price - create a real entry "
            f"(is_placeholder=False) before it can be approved"
        )
    entry.status = CatalogEntryStatus.APPROVED
    entry.approved_by = actor
    entry.approved_at = _db_now(db)
    db.commit()
    db.refresh(entry)
    log_event(
        db, actor=actor, action="billing.price_catalog_entry_approved", target=f"price_catalog_entry:{entry.id}",
        after={"plan_code": entry.plan_code, "catalog_version": entry.catalog_version},
    )
    return entry


def _new_period(now: datetime) -> tuple[datetime, datetime]:
    return now, now + _PERIOD_LENGTH


def sync_subscription_to_zoikonex(db: Session, sub: Subscription) -> Subscription:
    """Architecture doc §9 "Subscription sync". Best-effort: a ZoikoNex
    outage must never block signup/plan-change, so a failure here is
    logged and swallowed rather than raised - sub.zoikonex_ref stays NULL,
    which is exactly what the reconciliation job's
    SUBSCRIPTION_MISSING_ZOIKONEX_REF check is for (retries naturally on
    the next sync_subscription_to_zoikonex call, e.g. a later plan
    change, without any dedicated retry/dead-letter machinery yet)."""
    from app.numbering.identity.models import Account

    account = db.query(Account).filter(Account.id == sub.account_id).first()
    account_type = account.account_type.value if account else "individual"

    try:
        result = zoikonex_adapter.sync_subscription(db, sub, account_type=account_type)
    except zoikonex_adapter.ZoikoNexError:
        return sub

    sub.zoikonex_ref = result["account_id"]
    db.add(
        ZoikoNexSyncEvent(
            account_id=sub.account_id,
            event_type=ZoikoNexSyncEventType.SUBSCRIPTION_SYNC,
            zoikonex_ref=result["account_id"],
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
    a downstream billing sync (real or unreachable) had a problem.

    Cost estimation still comes from Zoiko Local's own calling-rate card,
    not a real ZoikoNex rating decision - see
    zoikonex_adapter.rate_usage_event's docstring for why."""
    rating = zoikonex_adapter.rate_usage_event(
        db, event_type=usage_event.event_type, quantity=float(usage_event.quantity),
        unit=usage_event.unit, country_band=usage_event.country_band,
    )
    usage_event.estimated_cost_cents = rating["estimated_cost_cents"]
    if rating["estimated_cost_cents"] is not None:
        # P0-8 "rating versioning" - only stamped when a rate table
        # actually applied (never for an event_type with no CallingRate
        # coverage yet - leaving rate_meter_version NULL there is the honest
        # "not rated at all" state, not "rated under version X").
        usage_event.rate_meter_version = zoikonex_adapter.CALLING_RATE_METER_VERSION
        usage_event.rated_at = _db_now(db)

    sub = db.query(Subscription).filter(Subscription.account_id == usage_event.account_id).first()
    try:
        result = zoikonex_adapter.sync_usage_event(
            db, sub, usage_event.id,
            event_type=usage_event.event_type, quantity=float(usage_event.quantity), unit=usage_event.unit,
        )
    except zoikonex_adapter.ZoikoNexError:
        result = {}

    # Real ZoikoNex rating (not the local estimate above) - only for
    # call_seconds, since that's the only event type with a real,
    # already-decided price (Zoiko Local's own CallingRate card). Every
    # other event_type has no rate table yet, so there's nothing real to
    # submit - see zoikonex_adapter.rate_usage_event's docstring.
    rated = {}
    if rating["estimated_cost_cents"] is not None:
        try:
            rated = zoikonex_adapter.rate_usage_in_zoikonex(
                db, sub, usage_event, amount_minor_units=rating["estimated_cost_cents"],
            )
        except zoikonex_adapter.ZoikoNexError:
            rated = {}

    db.add(
        ZoikoNexSyncEvent(
            account_id=usage_event.account_id,
            event_type=ZoikoNexSyncEventType.USAGE_SYNC,
            zoikonex_ref=result.get("zoikonex_ref"),
            payload={
                "usage_event_id": usage_event.id, "event_type": usage_event.event_type,
                "quantity": float(usage_event.quantity), "unit": usage_event.unit,
                "estimated_cost_cents": rating["estimated_cost_cents"],
                "zoikonex_rated_charge_id": rated.get("rated_charge_id"),
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


class TestAccountRestrictedError(Exception):
    """Raised when an account flagged is_test attempts a real-money
    ZoikoNex action (Commercial Billing Operating Standard doc §14/§T) -
    see app.numbering.identity.models.Account.is_test's docstring. A
    separate class from app.numbering.numbers.service's identically-named
    error (same concept, but importing across that pair would be
    circular - numbering already imports this module)."""


def _assert_not_test_account(db: Session, account_id: str) -> None:
    from app.numbering.identity.models import Account

    account = db.query(Account).filter(Account.id == account_id).first()
    if account is not None and account.is_test:
        raise TestAccountRestrictedError(
            f"Account {account_id} is flagged is_test and cannot be billed for real"
        )


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


class ReconciliationExceptionNotFoundError(Exception):
    """Raised when resolving a reconciliation exception id that doesn't exist."""


def run_zoikonex_reconciliation(db: Session) -> ZoikoNexReconciliationRun:
    """Architecture doc §9: "daily reconciliation jobs... exceptions must
    enter an operations queue." Turns the same drift
    get_zoikonex_reconciliation_summary already counts into individual,
    resolvable ZoikoNexReconciliationException rows, and persists the run
    itself (see ZoikoNexReconciliationRun's docstring for why). Idempotent
    against repeated drift: a record already sitting in an unresolved
    exception from a prior run does not get a duplicate row here - only
    genuinely new drift is added, so exceptions_found on this run means
    "newly found," not "total open"."""
    from app.media.models import CallRecord
    from app.usage.models import UsageEvent

    already_open: dict[ZoikoNexReconciliationExceptionType, set[str]] = {
        ZoikoNexReconciliationExceptionType.SUBSCRIPTION_MISSING_ZOIKONEX_REF: set(),
        ZoikoNexReconciliationExceptionType.USAGE_EVENT_MISSING_SYNC: set(),
        ZoikoNexReconciliationExceptionType.CALL_RECORD_MISSING_USAGE_EVENT: set(),
        ZoikoNexReconciliationExceptionType.LATE_USAGE_EVENT: set(),
    }
    for exc in db.query(ZoikoNexReconciliationException).filter(
        ZoikoNexReconciliationException.resolved_at.is_(None)
    ):
        already_open[exc.exception_type].add(exc.subject_id)

    run = ZoikoNexReconciliationRun()
    db.add(run)
    db.flush()  # populate run.id (Python-side default) for the exceptions' FK

    new_exceptions: list[ZoikoNexReconciliationException] = []

    subscriptions = db.query(Subscription).all()
    run.total_subscriptions = len(subscriptions)
    for sub in subscriptions:
        if sub.zoikonex_ref is not None:
            continue
        run.unsynced_subscriptions += 1
        if sub.id in already_open[ZoikoNexReconciliationExceptionType.SUBSCRIPTION_MISSING_ZOIKONEX_REF]:
            continue
        new_exceptions.append(
            ZoikoNexReconciliationException(
                run_id=run.id,
                account_id=sub.account_id,
                exception_type=ZoikoNexReconciliationExceptionType.SUBSCRIPTION_MISSING_ZOIKONEX_REF,
                subject_id=sub.id,
                detail=f"Subscription {sub.id} has no zoikonex_ref - subscription sync never completed.",
            )
        )

    synced_usage_event_ids = {
        row[0]
        for row in db.query(ZoikoNexSyncEvent.payload["usage_event_id"].astext)
        .filter(ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.USAGE_SYNC)
        .all()
    }
    usage_events = db.query(UsageEvent).all()
    run.total_usage_events = len(usage_events)
    for event in usage_events:
        if event.id in synced_usage_event_ids:
            continue
        run.unsynced_usage_events += 1
        if event.id in already_open[ZoikoNexReconciliationExceptionType.USAGE_EVENT_MISSING_SYNC]:
            continue
        new_exceptions.append(
            ZoikoNexReconciliationException(
                run_id=run.id,
                account_id=event.account_id,
                exception_type=ZoikoNexReconciliationExceptionType.USAGE_EVENT_MISSING_SYNC,
                subject_id=event.id,
                detail=f"Usage event {event.id} ({event.event_type}) has no matching ZoikoNex usage-sync ledger row.",
            )
        )

    # Third leg: carrier evidence (CallRecord, written from Twilio's own
    # status-callback webhook - see app.media.service.update_call_status)
    # vs. Zoiko Local's own usage metering for that same call. The
    # matching UsageEvent's idempotency_key is always
    # f"call_seconds:{provider_call_sid}" (see update_call_status) - no
    # join table needed, just that exact string.
    call_usage_events_by_key = {
        event.idempotency_key: event
        for event in db.query(UsageEvent).filter(UsageEvent.event_type == "call_seconds").all()
    }
    completed_calls = (
        db.query(CallRecord)
        .filter(
            CallRecord.status == "completed",
            CallRecord.duration.isnot(None),
            CallRecord.account_id.isnot(None),
            CallRecord.provider_call_sid.isnot(None),
        )
        .all()
    )
    run.total_completed_calls = len(completed_calls)
    for call in completed_calls:
        usage_event = call_usage_events_by_key.get(f"call_seconds:{call.provider_call_sid}")
        if usage_event is None:
            run.unmatched_completed_calls += 1
            if call.id in already_open[ZoikoNexReconciliationExceptionType.CALL_RECORD_MISSING_USAGE_EVENT]:
                continue
            new_exceptions.append(
                ZoikoNexReconciliationException(
                    run_id=run.id,
                    account_id=call.account_id,
                    exception_type=ZoikoNexReconciliationExceptionType.CALL_RECORD_MISSING_USAGE_EVENT,
                    subject_id=call.id,
                    detail=(
                        f"Call {call.provider_call_sid} completed ({call.duration}s, carrier-confirmed) "
                        f"but has no matching call_seconds usage event."
                    ),
                )
            )
            continue

        # P0-8 "late-event policy" - the usage event exists, but arrived
        # well after the call it bills for actually completed. Flagged
        # even though it's matched - a late event that lands after its
        # billing period has already invoiced needs a human decision, not
        # a silent pass. LATE_EVENT_THRESHOLD is deliberately generous (not
        # a tight SLA check) since this is about "did this miss a billing
        # cycle," not real-time metering latency.
        delay = usage_event.created_at - call.created_at
        if delay <= LATE_EVENT_THRESHOLD:
            continue
        run.late_usage_events += 1
        if usage_event.id not in already_open[ZoikoNexReconciliationExceptionType.LATE_USAGE_EVENT]:
            new_exceptions.append(
                ZoikoNexReconciliationException(
                    run_id=run.id,
                    account_id=call.account_id,
                    exception_type=ZoikoNexReconciliationExceptionType.LATE_USAGE_EVENT,
                    subject_id=usage_event.id,
                    detail=(
                        f"Usage event for call {call.provider_call_sid} was recorded "
                        f"{delay.total_seconds() / 3600:.1f}h after the call completed."
                    ),
                )
            )

    run.exceptions_found = len(new_exceptions)
    for exc in new_exceptions:
        db.add(exc)
    db.commit()
    db.refresh(run)
    return run


def capture_wholesale_call_cost(db: Session, *, limit: int = 50) -> dict:
    """Commercial Billing Operating Standard P0-8 "retail vs wholesale
    reconciliation" - fetches Twilio's own real, documented Call resource
    price for completed calls that don't have a wholesale cost captured
    yet, and stores it on CallRecord.

    Staff-triggered, not a scheduled job - there is no cron/scheduler
    anywhere in this codebase yet (same posture as run_zoikonex_
    reconciliation above). limit bounds how many calls one run fetches, so
    a large backlog doesn't turn one staff click into an unbounded burst of
    Twilio API calls.

    Twilio rates calls asynchronously, so price can still be None
    shortly after a call ends - those calls are left alone (not marked
    permanently failed) and simply get picked up by a later run."""
    from app.media.models import CallRecord

    calls = (
        db.query(CallRecord)
        .filter(
            CallRecord.status == "completed",
            CallRecord.provider_call_sid.isnot(None),
            CallRecord.wholesale_cost_cents.is_(None),
        )
        .order_by(CallRecord.created_at.asc())
        .limit(limit)
        .all()
    )

    captured = 0
    not_yet_rated = 0
    errors = 0
    for call in calls:
        try:
            details = telecom.get_call(call.provider_call_sid)
        except telecom.TelecomError:
            errors += 1
            continue

        price = details.get("price")
        price_unit = details.get("price_unit")
        if price is None or price_unit is None:
            not_yet_rated += 1
            continue

        call.wholesale_cost_cents = round(abs(float(price)) * 100)
        call.wholesale_currency = price_unit.upper()
        db.commit()
        db.refresh(call)
        log_event(
            db, actor="system:wholesale_cost_capture", action="call.wholesale_cost_captured",
            target=f"call_record:{call.id}", account_id=call.account_id,
            after={"wholesale_cost_cents": call.wholesale_cost_cents, "wholesale_currency": call.wholesale_currency},
        )
        captured += 1

    return {
        "calls_checked": len(calls),
        "captured": captured,
        "not_yet_rated": not_yet_rated,
        "errors": errors,
    }


def get_wholesale_reconciliation_summary(db: Session) -> dict:
    """Retail-vs-wholesale comparison for completed calls with a rated
    call_seconds UsageEvent - the other half of P0-8 alongside
    capture_wholesale_call_cost. Reports honestly on coverage gaps
    (calls with no wholesale cost captured yet) rather than excluding
    them silently, since a partial reconciliation that looks complete is
    worse than one that visibly isn't."""
    from app.media.models import CallRecord
    from app.usage.models import UsageEvent

    completed_calls = (
        db.query(CallRecord)
        .filter(CallRecord.status == "completed", CallRecord.provider_call_sid.isnot(None))
        .all()
    )
    usage_by_key = {
        event.idempotency_key: event
        for event in db.query(UsageEvent).filter(UsageEvent.event_type == "call_seconds").all()
    }

    calls_with_wholesale_cost = 0
    calls_missing_wholesale_cost = 0
    retail_cost_cents = 0
    wholesale_cost_cents = 0
    currencies_seen: set[str] = set()

    for call in completed_calls:
        if call.wholesale_cost_cents is None:
            calls_missing_wholesale_cost += 1
            continue
        calls_with_wholesale_cost += 1
        wholesale_cost_cents += call.wholesale_cost_cents
        if call.wholesale_currency:
            currencies_seen.add(call.wholesale_currency)

        usage_event = usage_by_key.get(f"call_seconds:{call.provider_call_sid}")
        if usage_event is not None and usage_event.estimated_cost_cents is not None:
            retail_cost_cents += usage_event.estimated_cost_cents

    # A single margin number only means something if every wholesale cost
    # captured so far is in the same currency - reported honestly instead
    # of silently summing across currencies if that's ever not true.
    single_currency = next(iter(currencies_seen)) if len(currencies_seen) == 1 else None
    return {
        "calls_with_wholesale_cost": calls_with_wholesale_cost,
        "calls_missing_wholesale_cost": calls_missing_wholesale_cost,
        "retail_cost_cents": retail_cost_cents,
        "wholesale_cost_cents": wholesale_cost_cents,
        "currency": single_currency,
        "mixed_currencies": sorted(currencies_seen) if len(currencies_seen) > 1 else None,
    }


def list_zoikonex_reconciliation_runs(db: Session, *, limit: int = 200) -> list[ZoikoNexReconciliationRun]:
    return (
        db.query(ZoikoNexReconciliationRun)
        .order_by(ZoikoNexReconciliationRun.created_at.desc())
        .limit(limit)
        .all()
    )


def list_zoikonex_reconciliation_exceptions(
    db: Session, *, resolved: bool | None = None, limit: int = 200
) -> list[ZoikoNexReconciliationException]:
    query = db.query(ZoikoNexReconciliationException)
    if resolved is True:
        query = query.filter(ZoikoNexReconciliationException.resolved_at.isnot(None))
    elif resolved is False:
        query = query.filter(ZoikoNexReconciliationException.resolved_at.is_(None))
    return query.order_by(ZoikoNexReconciliationException.created_at.desc()).limit(limit).all()


def resolve_zoikonex_reconciliation_exception(
    db: Session, exception_id: str, *, actor: str, reason: str
) -> ZoikoNexReconciliationException:
    """SUPER_ADMIN-only manual override (Architecture doc §10 "manual
    override reasons" under Business controls) - same bar as
    simulate_zoikonex_payment_event since both are money-adjacent state
    changes, not read-only diagnostics."""
    exc = (
        db.query(ZoikoNexReconciliationException)
        .filter(ZoikoNexReconciliationException.id == exception_id)
        .first()
    )
    if exc is None:
        raise ReconciliationExceptionNotFoundError(f"No reconciliation exception {exception_id!r}")

    exc.resolved_at = _db_now(db)
    exc.resolved_by = actor
    exc.resolution_reason = reason
    db.commit()
    db.refresh(exc)

    log_event(
        db, actor=actor, action="zoikonex.reconciliation_exception_resolved",
        target=f"zoikonex_reconciliation_exception:{exc.id}",
        after={"exception_type": exc.exception_type.value, "subject_id": exc.subject_id, "reason": reason},
    )
    return exc


class ZoikoNexBillingCycleError(Exception):
    """Raised when a billing cycle can't even start - e.g. the
    subscription has never successfully synced to ZoikoNex, so there's no
    account_id/customer_id to bill against. Distinct from a mid-pipeline
    ZoikoNexError (rating/invoice/payment-intent/authorise), which aborts
    the cycle the same way but with a different, already-diagnosed cause."""


class NonCommercialAccountError(Exception):
    """Raised when an account whose billing_classification/billing_source
    forbids a direct Zoiko Local charge (Commercial Billing Operating
    Standard P0-4/P0-5: "non-commercial classes cannot create live
    customer charges" + "no duplicate direct Zoiko Local charge for the
    same entitlement/period; billing_source references bundle/order")
    reaches run_billing_cycle. Mirrors app.numbering.numbers.service's
    identically-named gate on the number-purchase Stripe Checkout path -
    this is the equivalent gate for the ZoikoNex subscription-billing
    path, the OTHER real place this codebase creates a live charge."""


def _assert_direct_commercial_account(db: Session, account_id: str) -> None:
    from app.numbering.identity.models import Account, AccountBillingClassification, AccountBillingSource

    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None or account.billing_classification != AccountBillingClassification.COMMERCIAL_STANDALONE:
        classification = account.billing_classification.value if account else "unknown"
        raise NonCommercialAccountError(
            f"Account billing_classification {classification!r} cannot create a live ZoikoNex charge"
        )
    if account.billing_source != AccountBillingSource.DIRECT_ZOIKO_LOCAL:
        raise NonCommercialAccountError(
            f"Account billing_source {account.billing_source.value!r} is not billed directly by Zoiko "
            f"Local - running a direct billing cycle here would double-charge alongside that source"
        )


def run_billing_cycle(db: Session, account_id: str, *, actor: str) -> dict:
    """Architecture doc §9's rating -> invoice -> payment pipeline, priced
    from PriceCatalogEntry (Commercial Billing Operating Standard P0-1 -
    see that model's docstring). Staff-triggered on demand - no scheduler
    exists in this codebase yet, same posture as run_synthetic_checks and
    run_zoikonex_reconciliation.

    Stages, in order: register the plan in ZoikoNex's catalog (once, lazily,
    the first time any account on that plan is billed) -> open a bill cycle
    -> create + issue an invoice carrying one line item (the plan's catalog
    price) -> close the bill cycle -> create a payment intent for the
    invoice total -> authorise it -> attempt capture.

    Every stage after catalog registration can fail independently; each
    failure raises immediately (nothing here is worth attempting out of
    order) EXCEPT capture, which is allowed to fail per
    zoikonex_adapter.capture_payment_intent's docstring - a confirmed real
    bug in ZoikoNex's own payments<->evidence-ledger gRPC wrapper, not
    something this codebase can fix. A failed capture still returns a
    result describing everything that DID succeed (invoice issued, payment
    authorised) rather than raising, since "authorised but not yet
    captured" is a real, reportable, non-broken state - not an error.

    A plan with no catalog entry, or a zero-amount entry (free_trial), is
    skipped before any ZoikoNex call is made - there is nothing to bill.
    A PLACEHOLDER catalog entry (is_placeholder=True - see
    PriceCatalogEntry's docstring) is only chargeable in a development
    environment - outside development this raises rather than silently
    charging a fake test price, the same "no ad-hoc invented price" rule
    P0-1 exists for. A real (is_placeholder=False) entry must additionally
    be APPROVED before it's chargeable outside development. Checked before
    the commercial-account gate below since it's the cheapest possible
    reason to skip, same ordering rationale as the number-purchase quota
    check elsewhere in this codebase.

    Commercial Billing Operating Standard P0-4/P0-5: refuses to run at all
    for an account whose billing_classification isn't COMMERCIAL_STANDALONE
    or whose billing_source isn't DIRECT_ZOIKO_LOCAL (see
    NonCommercialAccountError) - a DEMO/SANDBOX/QA account must never get
    a live ZoikoNex charge, and an account meant to be billed through a
    bundle/partner/legacy path must never ALSO get charged directly here.
    """
    # Commercial Billing Operating Standard doc §32.1 - checked before any
    # ZoikoNex call, whether this is invoked directly or via
    # approve_billing_action's staged execution.
    assert_kill_switch_not_active(db, KillSwitchScope.PAYMENTS_BILLING)
    _assert_not_test_account(db, account_id)

    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)
    catalog_entry = get_active_price_catalog_entry(db, plan.plan_code)

    if catalog_entry is None or catalog_entry.amount_minor_units <= 0:
        return {"billed": False, "reason": f"plan {plan.plan_code!r} has no price catalog entry to bill"}
    _assert_direct_commercial_account(db, account_id)
    if settings.environment != "development":
        if catalog_entry.is_placeholder:
            raise ZoikoNexBillingCycleError(
                f"Cannot bill plan {plan.plan_code!r} outside development: its price catalog entry "
                f"({catalog_entry.catalog_version}) is still a placeholder - real pricing must be "
                f"decided and approved first (Commercial Billing Operating Standard P0-1)."
            )
        if catalog_entry.status != CatalogEntryStatus.APPROVED:
            raise ZoikoNexBillingCycleError(
                f"Cannot bill plan {plan.plan_code!r} outside development: its price catalog entry "
                f"({catalog_entry.catalog_version}) is not yet APPROVED."
            )

    amount_minor_units = catalog_entry.amount_minor_units
    currency_code = catalog_entry.currency_code

    if sub.zoikonex_account_id is None:
        sub = sync_subscription_to_zoikonex(db, sub)
    if sub.zoikonex_account_id is None:
        raise ZoikoNexBillingCycleError(
            "Cannot run a billing cycle: this subscription has never synced to ZoikoNex "
            "(no account_id) - check ZoikoNex connectivity and retry."
        )

    zoikonex_adapter.register_plan_in_catalog(db, plan, amount_minor_units=amount_minor_units, currency_code=currency_code)

    bill_cycle = zoikonex_adapter.open_bill_cycle(sub)
    invoice = zoikonex_adapter.create_invoice(sub, bill_cycle["bill_cycle_id"], currency_code=currency_code)
    # create_invoice's Idempotency-Key is deterministic per (sub.id,
    # current_period_start), so a retry of an already-issued invoice
    # replays the SAME invoice_id - but confirmed live, that replayed
    # response body is frozen at first-creation time (always "DRAFT"),
    # even after the invoice has since been issued. get_invoice does a
    # live read instead of trusting create_invoice's own return value here.
    live_invoice = zoikonex_adapter.get_invoice(invoice["invoice_id"])
    if live_invoice["status"] == "DRAFT":
        # First run for this subscription+period. ISSUED invoices are Class
        # A immutable (ZN-ADR-012) - adding a line item or re-issuing one
        # would fail with a 422, so this branch only runs once per period.
        # tax comes from a REAL ZoikoNex tax-decision call - always 0 right
        # now because it resolves against TAX_PLACEHOLDER_JURISDICTION_CODE's
        # 0% policy (see that constant's docstring: real tax rates are a
        # legal/compliance decision nobody has made yet, not something to
        # invent the way a subscription price got a placeholder).
        tax = zoikonex_adapter.determine_tax_for_invoice_line(
            invoice_id=invoice["invoice_id"], taxable_amount_minor_units=amount_minor_units,
            currency_code=currency_code,
        )
        description_suffix = " (TEST PLACEHOLDER PRICE, not a real charge)" if catalog_entry.is_placeholder else ""
        zoikonex_adapter.add_invoice_line_item(
            invoice["invoice_id"],
            description=f"{plan.name} - monthly subscription{description_suffix}",
            amount_minor_units=amount_minor_units,
            tax_amount_minor_units=tax.get("tax_amount_minor_units"),
            line_key="plan-fee",
        )
        issued = zoikonex_adapter.issue_invoice(invoice["invoice_id"])
    else:
        issued = {"status": live_invoice["status"]}
    try:
        zoikonex_adapter.close_bill_cycle(bill_cycle["bill_cycle_id"])
        bill_cycle_closed, bill_cycle_close_error = True, None
    except zoikonex_adapter.ZoikoNexError as e:
        # Confirmed real ZoikoNex-side bug (GetBillCycle NULL-scan - see
        # close_bill_cycle's docstring) - the invoice is already issued and
        # immutable regardless of whether the bill cycle formally closes,
        # so this must not block payment collection below.
        bill_cycle_closed, bill_cycle_close_error = False, str(e)

    db.add(
        ZoikoNexSyncEvent(
            account_id=account_id,
            event_type=ZoikoNexSyncEventType.INVOICE_GENERATED,
            zoikonex_ref=invoice["invoice_id"],
            payload={
                "plan_code": plan.plan_code, "amount_minor_units": amount_minor_units,
                "catalog_version": catalog_entry.catalog_version,
                "bill_cycle_id": bill_cycle["bill_cycle_id"], "status": issued["status"],
                "placeholder_price": catalog_entry.is_placeholder, "bill_cycle_closed": bill_cycle_closed,
                "bill_cycle_close_error": bill_cycle_close_error,
            },
        )
    )
    db.commit()

    intent = zoikonex_adapter.create_payment_intent(
        sub, invoice["invoice_id"], amount_minor_units=amount_minor_units, currency_code=currency_code,
    )
    zoikonex_adapter.authorise_payment_intent(intent["payment_intent_id"])

    result = {
        "billed": True, "plan_code": plan.plan_code, "amount_minor_units": amount_minor_units,
        "invoice_id": invoice["invoice_id"], "payment_intent_id": intent["payment_intent_id"],
        "invoice_status": issued["status"], "payment_status": "authorised", "captured": False,
        "capture_error": None, "bill_cycle_closed": bill_cycle_closed, "bill_cycle_close_error": bill_cycle_close_error,
    }
    try:
        zoikonex_adapter.capture_payment_intent(intent["payment_intent_id"])
        result["captured"] = True
        result["payment_status"] = "captured"
    except zoikonex_adapter.ZoikoNexCaptureFailedError as e:
        # Confirmed real ZoikoNex-side bug (evidence-ledger gRPC marshaling) -
        # authorised is a genuinely successful, reportable outcome on its own.
        result["capture_error"] = str(e)

    db.add(
        ZoikoNexSyncEvent(
            account_id=account_id,
            event_type=ZoikoNexSyncEventType.PAYMENT_COLLECTED,
            zoikonex_ref=intent["payment_intent_id"],
            payload={k: v for k, v in result.items() if k != "billed"},
        )
    )
    db.commit()

    log_event(
        db, actor=actor, action="billing.cycle_run", target=f"subscription:{sub.id}",
        after={"invoice_id": invoice["invoice_id"], "payment_status": result["payment_status"]},
    )
    return result


def issue_invoice_credit_note(
    db: Session, account_id: str, invoice_id: str, *, amount_minor_units: int, reason: str, actor: str
) -> dict:
    """Corrects an over-billed ISSUED invoice (ZN-ADR-012: the invoice
    itself can never be edited once issued - see
    zoikonex_adapter.create_credit_note's docstring). Staff-triggered,
    per-correction - unlike run_billing_cycle's deterministic idempotency
    keys, a fresh UUID here since a real operator issuing two separate
    credit notes against the same invoice for two different reasons is a
    legitimate scenario, not a retry."""
    assert_kill_switch_not_active(db, KillSwitchScope.PAYMENTS_BILLING)
    _assert_not_test_account(db, account_id)
    result = zoikonex_adapter.create_credit_note(
        invoice_id, reason_code="CUSTOMER_REQUEST", amount_minor_units=amount_minor_units,
        reason_description=reason, idempotency_key=str(uuid.uuid4()),
    )
    db.add(
        ZoikoNexSyncEvent(
            account_id=account_id, event_type=ZoikoNexSyncEventType.CREDIT_NOTE_ISSUED,
            zoikonex_ref=result["credit_note_id"],
            payload={"invoice_id": invoice_id, "amount_minor_units": amount_minor_units, "reason": reason},
        )
    )
    db.commit()
    log_event(
        db, actor=actor, action="billing.credit_note_issued", target=f"invoice:{invoice_id}",
        after={"credit_note_id": result["credit_note_id"], "amount_minor_units": amount_minor_units, "reason": reason},
    )
    return result


def issue_invoice_debit_note(
    db: Session, account_id: str, invoice_id: str, *, amount_minor_units: int, reason: str, actor: str
) -> dict:
    """Corrects an under-billed ISSUED invoice - see
    issue_invoice_credit_note's docstring for the same rationale."""
    assert_kill_switch_not_active(db, KillSwitchScope.PAYMENTS_BILLING)
    _assert_not_test_account(db, account_id)
    result = zoikonex_adapter.create_debit_note(
        invoice_id, reason_code="UNDERBILLED", amount_minor_units=amount_minor_units,
        idempotency_key=str(uuid.uuid4()),
    )
    db.add(
        ZoikoNexSyncEvent(
            account_id=account_id, event_type=ZoikoNexSyncEventType.DEBIT_NOTE_ISSUED,
            zoikonex_ref=result["debit_note_id"],
            payload={"invoice_id": invoice_id, "amount_minor_units": amount_minor_units, "reason": reason},
        )
    )
    db.commit()
    log_event(
        db, actor=actor, action="billing.debit_note_issued", target=f"invoice:{invoice_id}",
        after={"debit_note_id": result["debit_note_id"], "amount_minor_units": amount_minor_units, "reason": reason},
    )
    return result


def refund_zoikonex_payment(
    db: Session, account_id: str, payment_intent_id: str, *, amount_minor_units: int, reason: str, actor: str
) -> dict:
    """Refunds a CAPTURED ZoikoNex payment (full or partial) - see
    zoikonex_adapter.create_refund's docstring. Given payment capture is
    currently broken on ZoikoNex's own side (see
    app.integrations.billing.zoikonex's module docstring), calling this
    against any payment intent in this environment correctly fails with
    ZoikoNexError (409 STATE_CONFLICT, "illegal payment state transition")
    rather than succeeding - confirmed live, not a bug in this function."""
    assert_kill_switch_not_active(db, KillSwitchScope.PAYMENTS_BILLING)
    _assert_not_test_account(db, account_id)
    result = zoikonex_adapter.create_refund(
        payment_intent_id, refund_amount_minor_units=amount_minor_units,
        reason_code="CUSTOMER_REQUEST", idempotency_key=str(uuid.uuid4()),
    )
    db.add(
        ZoikoNexSyncEvent(
            account_id=account_id, event_type=ZoikoNexSyncEventType.REFUND_ISSUED,
            zoikonex_ref=result["refund_id"],
            payload={"payment_intent_id": payment_intent_id, "amount_minor_units": amount_minor_units, "reason": reason},
        )
    )
    db.commit()
    log_event(
        db, actor=actor, action="billing.payment_refunded", target=f"payment_intent:{payment_intent_id}",
        after={"refund_id": result["refund_id"], "amount_minor_units": amount_minor_units, "reason": reason},
    )
    return result


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


class BillingActionRequestNotFoundError(Exception):
    """Raised when acting on a billing_action_requests row that doesn't exist."""


class BillingActionAlreadyResolvedError(Exception):
    """Raised when approving/rejecting a request that isn't PENDING anymore."""


class SelfApprovalNotAllowedError(Exception):
    """Commercial Billing Operating Standard doc §26 - "Approver... cannot
    self-approve where policy applies." Raised when the staff member
    approving a BillingActionRequest is the same one who requested it."""


def request_billing_action(
    db: Session, *, action_type: BillingActionType, payload: dict, requested_by: str
) -> BillingActionRequest:
    """Stages a credit note / debit note / refund / run-billing-cycle
    action instead of executing it immediately - see BillingActionRequest's
    docstring. Does not touch ZoikoNex at all; only approve_billing_action
    (by a *different* staff member) does."""
    request = BillingActionRequest(
        action_type=action_type, payload=payload, requested_by=requested_by,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    log_event(
        db, actor=requested_by, action="billing.action_requested",
        target=f"billing_action_request:{request.id}",
        after={"action_type": action_type.value, "payload": payload},
    )
    return request


def list_billing_action_requests(
    db: Session, *, status: BillingActionRequestStatus | None = None
) -> list[BillingActionRequest]:
    query = db.query(BillingActionRequest)
    if status is not None:
        query = query.filter(BillingActionRequest.status == status)
    return query.order_by(BillingActionRequest.created_at.desc()).all()


def _execute_billing_action(db: Session, request: BillingActionRequest, *, actor: str) -> dict:
    """Replays the staged payload through the real service function for
    request.action_type - the approver authorizes exactly what was
    requested (see BillingActionRequest.payload's docstring), never a
    re-typed summary of it."""
    payload = request.payload
    if request.action_type == BillingActionType.RUN_BILLING_CYCLE:
        return run_billing_cycle(db, payload["account_id"], actor=actor)
    if request.action_type == BillingActionType.CREDIT_NOTE:
        return issue_invoice_credit_note(
            db, payload["account_id"], payload["invoice_id"],
            amount_minor_units=payload["amount_minor_units"], reason=payload["reason"], actor=actor,
        )
    if request.action_type == BillingActionType.DEBIT_NOTE:
        return issue_invoice_debit_note(
            db, payload["account_id"], payload["invoice_id"],
            amount_minor_units=payload["amount_minor_units"], reason=payload["reason"], actor=actor,
        )
    if request.action_type == BillingActionType.REFUND:
        return refund_zoikonex_payment(
            db, payload["account_id"], payload["payment_intent_id"],
            amount_minor_units=payload["amount_minor_units"], reason=payload["reason"], actor=actor,
        )
    raise ValueError(f"Unhandled BillingActionType: {request.action_type}")  # pragma: no cover - exhaustive above


def approve_billing_action(db: Session, request_id: str, *, actor: str) -> BillingActionRequest:
    request = db.query(BillingActionRequest).filter(BillingActionRequest.id == request_id).with_for_update().first()
    if request is None:
        raise BillingActionRequestNotFoundError(f"Billing action request {request_id} not found")
    if request.status != BillingActionRequestStatus.PENDING:
        raise BillingActionAlreadyResolvedError(f"Billing action request {request_id} is already {request.status.value}")
    if actor == request.requested_by:
        raise SelfApprovalNotAllowedError(
            "The staff member who requested this action cannot also approve it - a different staff "
            "member must approve"
        )

    # Execute first, mark EXECUTED only once the real ZoikoNex call actually
    # succeeds - an exception from _execute_billing_action propagates and
    # this request stays PENDING (not silently marked done) for retry.
    result = _execute_billing_action(db, request, actor=actor)

    request.status = BillingActionRequestStatus.EXECUTED
    request.approved_by = actor
    request.result = result
    request.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    log_event(
        db, actor=actor, action="billing.action_approved",
        target=f"billing_action_request:{request.id}",
        after={"action_type": request.action_type.value, "result": result},
    )
    return request


def reject_billing_action(db: Session, request_id: str, *, actor: str, reason: str | None = None) -> BillingActionRequest:
    request = db.query(BillingActionRequest).filter(BillingActionRequest.id == request_id).with_for_update().first()
    if request is None:
        raise BillingActionRequestNotFoundError(f"Billing action request {request_id} not found")
    if request.status != BillingActionRequestStatus.PENDING:
        raise BillingActionAlreadyResolvedError(f"Billing action request {request_id} is already {request.status.value}")
    if actor == request.requested_by:
        raise SelfApprovalNotAllowedError(
            "The staff member who requested this action cannot also reject it - a different staff "
            "member must review it"
        )

    request.status = BillingActionRequestStatus.REJECTED
    request.approved_by = actor
    request.rejection_reason = reason
    request.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    log_event(
        db, actor=actor, action="billing.action_rejected",
        target=f"billing_action_request:{request.id}",
        after={"action_type": request.action_type.value, "reason": reason},
    )
    return request
