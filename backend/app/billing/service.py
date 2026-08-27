import logging
import uuid
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.models import (
    AIReceptionistAddonRate,
    BillingActionRequest,
    BillingActionRequestStatus,
    BillingActionType,
    BillingPeriod,
    CatalogEntryStatus,
    EntitlementValueType,
    PendingAccountCharge,
    PendingAccountChargeStatus,
    Plan,
    PlanChangeCheckoutSession,
    PlanChangeCheckoutSessionStatus,
    PlanEntitlement,
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
from app.core.errors import EntitlementError
# publish_subscription_plan_changed deliberately not imported here anymore -
# change_plan() below replaced that fire-and-forget call with
# publish_event_durably (see its own comment at the call site).
from app.events.service import (
    publish_payment_failed,
    publish_payment_restored,
    publish_subscription_canceled,
    publish_subscription_payment_event,
    publish_subscription_terminated,
)
from app.integrations.billing import zoikonex as zoikonex_adapter
from app.integrations.cache.redis import cache_get, cache_set
from app.integrations.telecom import twilio as telecom
from app.notifications.service import (
    notify_credit_or_refund_processed,
    notify_invoice_available,
    notify_payment_failed,
    notify_payment_reminder,
    notify_payment_succeeded,
    notify_plan_changed,
    notify_plan_started,
    notify_service_restored,
    notify_subscription_terminated,
    notify_trial_started,
    send_internal_alert,
)
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active

logger = logging.getLogger("zoiko.billing")

DEFAULT_PLAN_CODE = "free_trial"
# Global Plans, Pricing & Commercial Launch doc: annual billing is paid
# upfront for a full year, not 12 monthly rollovers - each BillingPeriod
# needs its own period length so an annual subscriber's current_period_end
# (and therefore run_billing_cycle's re-bill cadence) actually reflects
# what they're paying for.
_PERIOD_LENGTHS = {
    BillingPeriod.MONTHLY: timedelta(days=30),
    BillingPeriod.ANNUAL: timedelta(days=365),
}
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


class NumberQuotaExceededError(EntitlementError):
    """Raised when purchasing another number would exceed the account's
    plan's max_numbers - a Phase-1-local entitlement gate (Architecture
    doc §5's "Subscription and Entitlement" service), independent of
    ZoikoNex, which doesn't exist yet."""
    code = "RESOURCE_OVER_LIMIT"
    status_code = 402


class SeatQuotaExceededError(EntitlementError):
    """Raised when adding another team member would exceed the account's
    plan's max_team_seats."""
    code = "RESOURCE_OVER_LIMIT"
    status_code = 402


class AiReceptionistNotEntitledError(EntitlementError):
    """Raised by configure_routing when a customer tries to enable the
    per-number ai_receptionist_enabled toggle without their plan/add-on
    actually granting AI Receptionist - previously that toggle had no
    entitlement check at all (a Starter account could enable it for free)."""
    code = "ADDON_REQUIRED"
    status_code = 402


class TrialWriteRestrictedError(EntitlementError):
    """Raised by app.core.deps.require_paid_or_read_only for a TRIALING
    account attempting a write (non-GET) action on a gated feature router.
    Trial accounts can view every dashboard section (Home's own stat cards
    depend on read access to numbers/calls/voicemail/video/receptionist
    data), but real actions - buying a number, sending a message, placing
    a call - require an upgrade. Previously there was no gate here at
    all: a trial account had full write access to every feature."""
    code = "UPGRADE_REQUIRED"
    status_code = 402


_PLANS_CACHE_KEY = "billing:plans"
# Long TTL, no invalidation needed - Plan rows are only ever seeded via
# migration (see Plan's docstring), never mutated by any runtime service
# function, so there's no write path this cache could ever go stale
# against. list_plans is called on nearly every /dashboard/billing page
# load (see PlanResponse's schema, which doesn't even expose the
# zoikonex_* fields register_plan_in_catalog does mutate at runtime, so
# caching those too here is harmless for that response shape).
_PLANS_CACHE_TTL_SECONDS = 300


def _serialize_plan(plan: Plan) -> dict:
    return {
        "plan_code": plan.plan_code,
        "name": plan.name,
        "max_numbers": plan.max_numbers,
        "max_team_seats": plan.max_team_seats,
        "monthly_voice_minutes": plan.monthly_voice_minutes,
        "monthly_video_minutes": plan.monthly_video_minutes,
        "max_video_participants": plan.max_video_participants,
        "monthly_ai_summaries": plan.monthly_ai_summaries,
        "included_ai_receptionist_minutes": plan.included_ai_receptionist_minutes,
        "trial_days": plan.trial_days,
        "sort_order": plan.sort_order,
        "zoikonex_product_id": plan.zoikonex_product_id,
        "zoikonex_offer_id": plan.zoikonex_offer_id,
        "zoikonex_price_rule_id": plan.zoikonex_price_rule_id,
        "created_at": plan.created_at.isoformat() if plan.created_at else None,
    }


def _deserialize_plan(data: dict) -> Plan:
    return Plan(
        plan_code=data["plan_code"],
        name=data["name"],
        max_numbers=data["max_numbers"],
        max_team_seats=data["max_team_seats"],
        monthly_voice_minutes=data["monthly_voice_minutes"],
        monthly_video_minutes=data["monthly_video_minutes"],
        max_video_participants=data["max_video_participants"],
        monthly_ai_summaries=data["monthly_ai_summaries"],
        included_ai_receptionist_minutes=data["included_ai_receptionist_minutes"],
        trial_days=data["trial_days"],
        sort_order=data["sort_order"],
        zoikonex_product_id=data["zoikonex_product_id"],
        zoikonex_offer_id=data["zoikonex_offer_id"],
        zoikonex_price_rule_id=data["zoikonex_price_rule_id"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def list_plans(db: Session) -> list[Plan]:
    cached = cache_get(_PLANS_CACHE_KEY)
    if cached is not None:
        return [_deserialize_plan(row) for row in cached]
    plans = db.query(Plan).order_by(Plan.sort_order).all()
    cache_set(_PLANS_CACHE_KEY, [_serialize_plan(p) for p in plans], ttl_seconds=_PLANS_CACHE_TTL_SECONDS)
    return plans


def get_plan(db: Session, plan_code: str) -> Plan:
    """Single-plan lookup - called from ~10 hot paths (quota checks, every
    call's time-limit lookup, checkout, usage summary), unlike list_plans
    above which only serves the billing page. Reuses that same cache
    (_serialize_plan/_deserialize_plan, same TTL) keyed per plan_code -
    plans are only ever seeded via migration, never mutated at runtime, so
    there's no invalidation path needed, same assumption list_plans already
    relies on."""
    cache_key = f"plan:{plan_code}"
    cached = cache_get(cache_key)
    if cached is not None:
        return _deserialize_plan(cached)
    plan = db.query(Plan).filter(Plan.plan_code == plan_code).first()
    if plan is None:
        raise PlanNotFoundError(f"No such plan: {plan_code!r}")
    cache_set(cache_key, _serialize_plan(plan), ttl_seconds=_PLANS_CACHE_TTL_SECONDS)
    return plan


class PriceUnavailableForCheckoutError(Exception):
    """Raised when a plan has no real, non-placeholder ACTIVE price to
    charge - refusing to create a Stripe Checkout Session for an invented
    or placeholder amount (Production Readiness Standard doc: "no charge
    without an active price book")."""


class PlanChangeCheckoutSessionNotFoundError(Exception):
    """Raised when a Stripe webhook references a checkout session id this
    system never created - either a forged/unrelated event or a record
    that predates this feature."""


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


class CannotActivateEntryError(Exception):
    """Raised when trying to activate a catalog entry that isn't APPROVED
    yet (PROPOSED/ACTIVE/RETIRED can't jump straight to ACTIVE - see
    activate_price_catalog_entry)."""


def get_active_price_catalog_entry(
    db: Session, plan_code: str, *, market: str = "GLOBAL", billing_period: BillingPeriod = BillingPeriod.MONTHLY,
) -> PriceCatalogEntry | None:
    """The "current" price for a plan+market+billing_period. Prefers the
    entry actually promoted to ACTIVE (see activate_price_catalog_entry) -
    Production Readiness & Go-Live Decision Standard §2.3/Table 8's
    PROPOSED/APPROVED/ACTIVE/RETIRED lifecycle. Falls back to the most
    recently created entry for this plan+market+period regardless of
    status when nothing has ever been activated yet - preserves the
    original P0-1 dev/test convenience (create a catalog entry, bill
    against it immediately in development) without requiring every
    test/dev workflow to also call activate. run_billing_cycle's own
    status/is_placeholder checks are what actually keep a non-ACTIVE entry
    from being charged outside development."""
    active = (
        db.query(PriceCatalogEntry)
        .filter(
            PriceCatalogEntry.plan_code == plan_code, PriceCatalogEntry.market == market,
            PriceCatalogEntry.billing_period == billing_period,
            PriceCatalogEntry.status == CatalogEntryStatus.ACTIVE,
        )
        .first()
    )
    if active is not None:
        return active
    return (
        db.query(PriceCatalogEntry)
        .filter(
            PriceCatalogEntry.plan_code == plan_code, PriceCatalogEntry.market == market,
            PriceCatalogEntry.billing_period == billing_period,
        )
        .order_by(PriceCatalogEntry.created_at.desc())
        .first()
    )


def create_price_catalog_entry(
    db: Session, *, plan_code: str, catalog_version: str, amount_minor_units: int,
    currency_code: str = "USD", is_placeholder: bool = True, actor: str,
    price_book_version: str | None = None, market: str = "GLOBAL",
    billing_period: BillingPeriod = BillingPeriod.MONTHLY,
    effective_from: datetime | None = None, effective_to: datetime | None = None,
) -> PriceCatalogEntry:
    """SUPER_ADMIN-gated at the route - locking/changing a price is a
    commercial decision, the same bar as a calling-rate change
    (app.usage.service.upsert_calling_rate). Always creates a NEW row;
    never edits an existing catalog_version (Class A - see
    PriceCatalogEntry's docstring). Starts life as PROPOSED (the model
    default) regardless of caller - only approve_price_catalog_entry and
    activate_price_catalog_entry advance the lifecycle."""
    existing = (
        db.query(PriceCatalogEntry)
        .filter(
            PriceCatalogEntry.plan_code == plan_code, PriceCatalogEntry.catalog_version == catalog_version,
            PriceCatalogEntry.billing_period == billing_period,
        )
        .first()
    )
    if existing is not None:
        raise PriceCatalogEntryExistsError(
            f"Catalog version {catalog_version!r} ({billing_period.value}) already exists for plan {plan_code!r} - "
            f"use a new version"
        )
    entry = PriceCatalogEntry(
        plan_code=plan_code, catalog_version=catalog_version, amount_minor_units=amount_minor_units,
        currency_code=currency_code, is_placeholder=is_placeholder,
        price_book_version=price_book_version, market=market, billing_period=billing_period,
        effective_from=effective_from, effective_to=effective_to,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    log_event(
        db, actor=actor, action="billing.price_catalog_entry_created", target=f"plan:{plan_code}",
        after={
            "catalog_version": catalog_version, "amount_minor_units": amount_minor_units,
            "currency_code": currency_code, "is_placeholder": is_placeholder,
            "price_book_version": price_book_version, "market": market,
        },
    )
    return entry


def approve_price_catalog_entry(
    db: Session, entry_id: str, *, actor: str, approval_evidence: str,
) -> PriceCatalogEntry:
    """SUPER_ADMIN-gated at the route. Refuses to approve a placeholder
    entry (see CannotApprovePlaceholderError) - a real price decision must
    create its own real (is_placeholder=False) entry first.
    approval_evidence (Production Readiness Standard Table 8 - "Commercial/
    Finance approval ID and change authority") is the actual sign-off
    reference (e.g. a Commercial/Finance ticket or decision-record ID),
    distinct from approved_by/approved_at which just record who clicked
    approve in this system and when. APPROVED is not yet chargeable on its
    own - see activate_price_catalog_entry for the step that actually puts
    a version into effect."""
    entry = db.query(PriceCatalogEntry).filter(PriceCatalogEntry.id == entry_id).first()
    if entry is None:
        raise PriceCatalogEntryNotFoundError(f"No such price catalog entry: {entry_id!r}")
    if entry.is_placeholder:
        raise CannotApprovePlaceholderError(
            f"Catalog entry {entry_id!r} is a placeholder/test price - create a real entry "
            f"(is_placeholder=False) before it can be approved"
        )
    entry.status = CatalogEntryStatus.APPROVED
    entry.approval_evidence = approval_evidence
    entry.approved_by = actor
    entry.approved_at = _db_now(db)
    db.commit()
    db.refresh(entry)
    log_event(
        db, actor=actor, action="billing.price_catalog_entry_approved", target=f"price_catalog_entry:{entry.id}",
        after={"plan_code": entry.plan_code, "catalog_version": entry.catalog_version, "approval_evidence": approval_evidence},
    )
    return entry


def activate_price_catalog_entry(db: Session, entry_id: str, *, actor: str) -> PriceCatalogEntry:
    """Promotes an APPROVED entry to ACTIVE - the step that actually makes
    it the version run_billing_cycle will charge outside development
    (Production Readiness Standard §2.3/Table 8). At most one ACTIVE entry
    can exist per plan_code+market+billing_period: whatever was previously
    ACTIVE for that same plan_code+market+billing_period is moved to
    RETIRED in the same transaction, never deleted, so past invoices
    remain reproducible against the exact version that was active when
    they were issued.

    billing_period is included in this "previously active" scope
    (regression fix, 2026-08-19) - it was added to PriceCatalogEntry after
    this function was originally written, and without it here, activating
    a plan's ANNUAL entry would incorrectly retire that same plan's
    already-ACTIVE MONTHLY entry (and vice versa) purely because they
    share the same plan_code+market, which is wrong - they're two
    independent, simultaneously-valid prices."""
    entry = db.query(PriceCatalogEntry).filter(PriceCatalogEntry.id == entry_id).first()
    if entry is None:
        raise PriceCatalogEntryNotFoundError(f"No such price catalog entry: {entry_id!r}")
    if entry.status != CatalogEntryStatus.APPROVED:
        raise CannotActivateEntryError(
            f"Catalog entry {entry_id!r} is {entry.status.value!r}, not APPROVED - only an APPROVED entry can be activated"
        )
    previously_active = (
        db.query(PriceCatalogEntry)
        .filter(
            PriceCatalogEntry.plan_code == entry.plan_code, PriceCatalogEntry.market == entry.market,
            PriceCatalogEntry.billing_period == entry.billing_period,
            PriceCatalogEntry.status == CatalogEntryStatus.ACTIVE,
        )
        .all()
    )
    for old in previously_active:
        old.status = CatalogEntryStatus.RETIRED
    entry.status = CatalogEntryStatus.ACTIVE
    if entry.effective_from is None:
        entry.effective_from = _db_now(db)
    db.commit() 
    db.refresh(entry)
    log_event(
        db, actor=actor, action="billing.price_catalog_entry_activated", target=f"price_catalog_entry:{entry.id}",
        after={"plan_code": entry.plan_code, "market": entry.market, "catalog_version": entry.catalog_version},
        before={"retired_entry_ids": [old.id for old in previously_active]},
    )
    return entry


def _new_period(now: datetime, billing_period: BillingPeriod = BillingPeriod.MONTHLY) -> tuple[datetime, datetime]:
    return now, now + _PERIOD_LENGTHS[billing_period]


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

    # Real ZoikoNex rating (not the local estimate above) - fires for any
    # event_type that got a non-None estimate above (call_seconds,
    # ai_receptionist_minutes, number_month - each has a real,
    # already-decided price via CallingRate/AIUsageRate/NumberRate). Every
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
        sub.current_period_start, sub.current_period_end = _new_period(now, sub.billing_period)
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


def change_plan(
    db: Session, account_id: str, plan_code: str, *, actor: str, billing_period: BillingPeriod = BillingPeriod.MONTHLY,
) -> Subscription:
    plan = get_plan(db, plan_code)  # raises PlanNotFoundError for an invalid code
    sub = get_or_create_subscription(db, account_id)
    before_plan = sub.plan_code

    sub.plan_code = plan.plan_code
    sub.billing_period = billing_period
    if sub.status == SubscriptionStatus.TRIALING:
        # Deliberately choosing a plan ends the trial early - matches how
        # every real subscription product treats an explicit upgrade.
        sub.status = SubscriptionStatus.ACTIVE
        sub.trial_ends_at = None
    # Durable outbox write (see EventOutbox's docstring) instead of the
    # fire-and-forget publish_subscription_plan_changed used elsewhere -
    # a plan change is entitlement-critical (it changes what the account
    # can do right now), so it's one of the representative call sites
    # this pattern is scoped to, not every domain event. Written into the
    # SAME transaction as the plan_code/billing_period update below via
    # db.add() only (no commit here) - if this commit rolls back, the
    # event row never existed either.
    from app.events.service import publish_event_durably

    publish_event_durably(
        db, "zoiko.billing", "subscription.plan_changed", account_id,
        {"subscription_id": sub.id, "previous_plan": before_plan, "new_plan": sub.plan_code},
    )
    db.commit()
    db.refresh(sub)
    sync_subscription_to_zoikonex(db, sub)

    log_event(
        db, actor=actor, action="subscription.plan_changed", target=f"subscription:{sub.id}",
        before={"plan_code": before_plan}, after={"plan_code": sub.plan_code},
    )

    # Production Readiness Standard doc's "trial-abuse step-up model" - a
    # real paid plan is just as strong a "genuine paying customer" signal
    # as a completed number purchase (see app.risk.service.
    # _compute_baseline_risk_state), so it earns the same step-up.
    from app.risk.service import step_up_risk_state_after_plan_upgrade

    step_up_risk_state_after_plan_upgrade(db, account_id, plan.plan_code)

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


def create_plan_change_checkout_session(
    db: Session, account_id: str, plan_code: str, *, billing_period: BillingPeriod, actor: str,
) -> dict:
    """Customer-facing entry point for a plan upgrade going forward -
    change_plan() above is no longer called directly from a customer
    request (see billing/routes.py's change_plan route docstring for why
    it's kept for staff/internal use). Real money must be collected before
    the target plan's entitlements apply (Production Readiness Standard
    doc: "A payment-success UI is not the same as an authoritative paid
    invoice" - this system was skipping that distinction entirely for
    subscriptions, unlike numbers/ZoikoNex which at least record a pending
    charge). Returns the Stripe-hosted Checkout Session {id, url} the
    frontend must redirect the browser to; the plan itself only changes
    once handle_stripe_checkout_completed processes the resulting webhook."""
    plan = get_plan(db, plan_code)  # raises PlanNotFoundError for an invalid code

    price = get_active_price_catalog_entry(db, plan_code, billing_period=billing_period)
    if price is None or price.is_placeholder:
        raise PriceUnavailableForCheckoutError(
            f"No real, non-placeholder ACTIVE price is configured for {plan_code}/{billing_period.value}"
        )

    record = PlanChangeCheckoutSession(
        account_id=account_id, plan_code=plan.plan_code, billing_period=billing_period,
        stripe_session_id="",  # filled in below once Stripe returns the real session id
    )
    db.add(record)
    db.flush()  # assigns record.id without committing yet - needed for the metadata below

    from app.integrations.billing import stripe_checkout

    interval = "year" if billing_period == BillingPeriod.ANNUAL else "month"
    session = stripe_checkout.create_subscription_checkout_session(
        plan_name=plan.name, amount_cents=price.amount_minor_units, currency=price.currency_code.lower(),
        interval=interval,
        success_url=f"{settings.frontend_base_url}/dashboard/billing?checkout=success",
        cancel_url=f"{settings.frontend_base_url}/dashboard/billing?checkout=cancel",
        metadata={"checkout_record_id": record.id, "account_id": account_id, "plan_code": plan.plan_code},
    )
    record.stripe_session_id = session["id"]
    db.commit()

    log_event(
        db, actor=actor, action="subscription.plan_change_checkout_created",
        target=f"subscription_checkout:{record.id}",
        after={"plan_code": plan.plan_code, "billing_period": billing_period.value, "stripe_session_id": session["id"]},
    )
    return session


def handle_stripe_checkout_completed(
    db: Session, *, checkout_record_id: str, stripe_subscription_id: str | None = None,
) -> Subscription:
    """Called from the Stripe webhook once a subscription Checkout Session's
    payment actually succeeds. Idempotent on PlanChangeCheckoutSession.status
    - Stripe retries webhook delivery, so a second delivery of the same
    completed event must not re-apply (or double-notify) the plan change.

    stripe_subscription_id is the real, live Stripe Subscription object this
    Checkout Session created (mode="subscription" - Stripe manages its
    recurring charge itself from here). It's stored on our own Subscription
    so cancel_subscription later has something real to cancel - without it,
    nothing in this codebase could ever stop Stripe from continuing to
    charge a customer who canceled here. If the account already had a
    *different* real Stripe subscription (an earlier paid plan, since this
    system always creates a fresh Checkout Session per plan change rather
    than updating one in place), that old one is canceled here too -
    otherwise Stripe would have no idea it's been superseded and would keep
    billing both in parallel."""
    record = db.query(PlanChangeCheckoutSession).filter(PlanChangeCheckoutSession.id == checkout_record_id).first()
    if record is None:
        raise PlanChangeCheckoutSessionNotFoundError(f"No checkout session record {checkout_record_id!r}")

    if record.status == PlanChangeCheckoutSessionStatus.COMPLETED:
        return get_or_create_subscription(db, record.account_id)

    previous_stripe_subscription_id = get_or_create_subscription(db, record.account_id).stripe_subscription_id

    # Apply the plan change FIRST, and only mark this record COMPLETED once
    # that has genuinely succeeded - marking it complete first (as this used
    # to) would leave a paid checkout permanently stuck as "done" with the
    # plan never actually changed if change_plan raised, since the
    # idempotency check above would then skip retrying it on Stripe's next
    # webhook redelivery.
    sub = change_plan(
        db, record.account_id, record.plan_code, actor="stripe_checkout_webhook", billing_period=record.billing_period,
    )
    sub.stripe_subscription_id = stripe_subscription_id

    record.status = PlanChangeCheckoutSessionStatus.COMPLETED
    record.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sub)

    if previous_stripe_subscription_id and previous_stripe_subscription_id != stripe_subscription_id:
        from app.integrations.billing import stripe_checkout

        try:
            stripe_checkout.cancel_subscription(previous_stripe_subscription_id)
        except stripe_checkout.PaymentError:
            # The new plan is already paid for and applied - don't roll any
            # of that back over a cleanup failure. But a still-live old
            # Stripe subscription will keep charging the customer's card in
            # parallel with the new one until someone cancels it by hand,
            # so this needs a human, not a silent log line.
            logger.exception(
                "Failed to cancel superseded Stripe subscription %s for account %s after plan change to %s - "
                "it may still be actively charging this customer and needs manual cancellation in Stripe.",
                previous_stripe_subscription_id, record.account_id, record.plan_code,
            )
            send_internal_alert(
                db, event_name="bill_int.stripe_subscription_cancel_failed",
                summary=(
                    f"Account {record.account_id} changed plan to {record.plan_code}, but canceling its "
                    f"previous Stripe subscription {previous_stripe_subscription_id} failed. It may still be "
                    f"actively charging this customer - cancel it manually in the Stripe dashboard."
                ),
                console_link=f"{settings.public_base_url}/staff/accounts",
                tenant_reference=record.account_id,
            )

    return sub


def _count_owned_or_in_flight_numbers(db: Session, account_id: str, *, exclude_number_id: str | None = None) -> int:
    """Shared by assert_number_quota_available (raises) and get_usage_summary
    (reports) so the two can never disagree on what counts as "owned"."""
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

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
    return query.count()


def assert_number_quota_available(db: Session, account_id: str, *, exclude_number_id: str | None = None) -> None:
    """exclude_number_id excludes the number currently being (re-)purchased
    from its own count - a retry of a number already sitting in
    COMPLIANCE_PENDING for this same account (e.g. after a compliance case
    gets approved) isn't an ADDITIONAL number, so it must not count against
    the quota a second time and block its own retry."""
    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)
    owned_or_in_flight = _count_owned_or_in_flight_numbers(db, account_id, exclude_number_id=exclude_number_id)
    if owned_or_in_flight >= plan.max_numbers:
        raise NumberQuotaExceededError(
            f"Your {plan.name} plan allows up to {plan.max_numbers} number(s) - "
            f"upgrade your plan to purchase another."
        )


def get_included_number_ids(db: Session, account_id: str, *, exclude_number_id: str | None = None) -> set[str]:
    """Global Plans, Pricing & Commercial Launch doc: "Included local
    number: 1 standard eligible number per paid user." Previously this
    codebase read that as "the account's very first number is free,
    period" regardless of team size - the doc's own wording is explicit
    that the free-number POOL scales with paid seat count, so a 10-seat
    Business account is entitled to up to 10 free numbers, not 1.

    The pool size is the account's current seat count (db.query(User)...
    count(), same source assert_seat_quota_available already uses) - not
    separately capped against plan.max_team_seats, since seat count can
    never exceed that cap in the first place (assert_seat_quota_available
    blocks adding a member beyond it). No per-seat "claim" step exists -
    an included number isn't assigned to a specific user, it's simply
    "does the account currently have at least one open included slot,"
    same self-service pattern as before, just with a variable-size pool
    now instead of a hardcoded pool of 1.

    Free-trial accounts don't qualify - "paid user" is the doc's own
    wording - nor does a canceled subscription. The recurring "$4.99/month
    additional number" charge from the doc is NOT implemented here: this
    codebase only has one-time Stripe Checkout for numbers today, and
    turning that into real recurring per-number billing (subscription
    items, proration, invoice lines) is a distinct, larger change than
    "how many numbers are free" - out of scope for this fix.

    Single source of truth for "which numbers are the free ones" - returns
    the ids of the account's N earliest-acquired qualifying numbers, where
    N is the seat count (empty set if the account doesn't qualify at all,
    or owns none yet). Used both to decide whether purchasing a NEW number
    should be free (is_first_number_included below) and, at renewal time,
    whether a SPECIFIC already-owned number should still be treated as a
    free one (app.numbering.numbers.service.mark_number_renewed) - these
    used to be two independently-maintained implementations with different
    status sets and no shared subscription-qualification check, which
    could (and in the free-trial case, did) disagree on which number was
    really free."""
    from app.numbering.identity.models import User
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

    sub = get_or_create_subscription(db, account_id)
    if sub.plan_code == DEFAULT_PLAN_CODE or sub.status == SubscriptionStatus.CANCELED:
        return set()
    seat_count = db.query(User).filter(User.account_id == account_id).count()
    if seat_count <= 0:
        return set()
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
    earliest = query.order_by(PhoneNumber.created_at.asc()).limit(seat_count).all()
    return {n.id for n in earliest}


def is_first_number_included(db: Session, account_id: str, *, exclude_number_id: str | None = None) -> bool:
    """Would purchasing a new number right now be free - true whenever the
    account's included-number pool (get_included_number_ids) has room left
    for one more, i.e. fewer already-owned qualifying numbers than paid
    seats. The qualification check (plan/status) is repeated here (cheap -
    get_or_create_subscription is a single indexed lookup) rather than
    inferring it from an empty set, which is ambiguous on its own -
    "doesn't qualify at all" and "qualifies but owns nothing yet" both
    return an empty pool otherwise, exactly the bug a first version of
    this refactor shipped with: a free_trial account's number purchase was
    treated as included."""
    from app.numbering.identity.models import User

    sub = get_or_create_subscription(db, account_id)
    if sub.plan_code == DEFAULT_PLAN_CODE or sub.status == SubscriptionStatus.CANCELED:
        return False
    seat_count = db.query(User).filter(User.account_id == account_id).count()
    included_count = len(get_included_number_ids(db, account_id, exclude_number_id=exclude_number_id))
    return included_count < seat_count


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


class EntitlementRequiredError(Exception):
    """ZL-COM-ENT-001 §5-6 real gap fix - raised by assert_entitlement (and
    caught the same way SeatQuotaExceededError already is at each call
    site) when neither the account's base plan nor an active add-on grants
    the requested entitlement key."""

    def __init__(self, key: str, plan_code: str):
        self.key = key
        self.plan_code = plan_code
        super().__init__(
            f"Your current plan ({plan_code}) does not include {key!r} - upgrade to unlock this feature."
        )


def has_entitlement(db: Session, account_id: str, key: str) -> bool:
    """ZL-COM-ENT-001's core principle: 'No entitlement record means no
    runtime access' - deny-by-default. No PlanEntitlement row for this
    plan_code+key means False, not an error and not an implicit grant
    (this is why free_trial and enterprise plan_codes, which have no seeded
    rows yet, correctly deny every key rather than needing special-casing
    here). A CANCELED/TERMINATED subscription never has any entitlement,
    regardless of what row exists for its last plan_code."""
    sub = get_or_create_subscription(db, account_id)
    if sub.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.TERMINATED):
        return False
    row = (
        db.query(PlanEntitlement)
        .filter(PlanEntitlement.plan_code == sub.plan_code, PlanEntitlement.key == key)
        .first()
    )
    if row is None:
        return False
    if row.value_type == EntitlementValueType.BOOLEAN:
        return bool(row.bool_value)
    return (row.int_value or 0) > 0


def assert_entitlement(db: Session, account_id: str, key: str) -> None:
    if not has_entitlement(db, account_id, key):
        sub = get_or_create_subscription(db, account_id)
        raise EntitlementRequiredError(key, sub.plan_code)


class InvalidPaymentEventError(Exception):
    """Raised for an unrecognized simulated payment event type."""


class ZoikoNexRefNotFoundError(Exception):
    """Raised when an inbound ZoikoNex webhook references a zoikonex_ref
    that doesn't match any local Subscription."""


class BillingSuspendedError(EntitlementError):
    """Raised when an account's grace period has expired with unresolved
    PAST_DUE status - Architecture doc §9 "Graceful degradation": outbound
    calling, video, new purchases, and AI features may pause after dunning
    thresholds. Deliberately never raised for inbound calls or existing
    number ownership - see assert_billing_not_suspended's docstring."""
    code = "SUBSCRIPTION_SUSPENDED"
    status_code = 402


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


def _apply_payment_event(
    db: Session,
    sub: Subscription,
    event_type: str,
    *,
    actor: str,
    action: str,
    notif_idempotency_key: str | None = None,
) -> Subscription:
    """Provider-agnostic core of a payment_failed/payment_retry/
    payment_restored state transition - extracted from
    _apply_zoikonex_payment_event so the SAME PAST_DUE/grace-period
    machinery (Kafka events, notifications, audit log) can also drive off
    a REAL Stripe recurring-billing webhook, not just the ZoikoNex mock's
    simulated events. See handle_stripe_subscription_payment_webhook's
    docstring for why this mattered: this state machine already existed
    and was fully correct, but nothing ever fed it from Stripe's own
    invoice.payment_failed/invoice.paid - a real customer whose card
    failed on a live Stripe renewal charge stayed ACTIVE with full access
    forever, since only the ZoikoNex mock webhook (which nothing real
    calls yet) could ever trip PAST_DUE."""
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
    # it's evidence the provider is still trying, not a state transition itself.

    db.commit()
    db.refresh(sub)

    log_event(
        db, actor=actor, action=action, target=f"subscription:{sub.id}",
        before={"status": before_status.value}, after={"status": sub.status.value, "event_type": event_type},
    )
    publish_subscription_payment_event(
        account_id, subscription_id=sub.id, event_type=event_type, status=sub.status.value,
    )
    # Named events from the Architecture doc's §8 table, additive alongside
    # the generic subscription.payment_event above - not a replacement.
    if event_type == "payment_failed":
        publish_payment_failed(account_id, subscription_id=sub.id, reason=event_type)
    elif event_type == "payment_restored":
        publish_payment_restored(account_id, subscription_id=sub.id)

    from app.numbering.identity.models import User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        plan = get_plan(db, sub.plan_code)
        if event_type == "payment_failed":
            notify_payment_failed(
                db, account_id=account_id, account_email=owner.email, plan_name=plan.name,
                idempotency_key=notif_idempotency_key,
            )
        elif event_type == "payment_retry":
            notify_payment_reminder(
                db, account_id=account_id, account_email=owner.email, plan_name=plan.name,
                grace_period_ends_at=sub.grace_period_ends_at.strftime("%Y-%m-%d") if sub.grace_period_ends_at else "",
            )
        elif event_type == "payment_restored":
            notify_service_restored(
                db, account_id=account_id, account_email=owner.email, idempotency_key=notif_idempotency_key,
            )

    return sub


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
    used by both the staff-triggered simulator and the real webhook. Thin
    wrapper around _apply_payment_event that additionally records the
    ZoikoNexSyncEvent row this specific provider's sync trail needs."""
    account_id = sub.account_id
    # Email Communications System doc A-03 (BLOCKER) "idempotency" - a
    # retried ZoikoNex webhook delivering the same external_event_id twice
    # must not double-send this notification. No key for the staff-
    # triggered simulator path (external_event_id is None there by
    # construction) since a manual staff action isn't a retry risk.
    notif_idempotency_key = f"{event_type}:{external_event_id}" if external_event_id else None

    sub = _apply_payment_event(
        db, sub, event_type, actor=actor, action=action, notif_idempotency_key=notif_idempotency_key,
    )

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


def handle_stripe_subscription_payment_webhook(
    db: Session, *, stripe_subscription_id: str, event_type: str, stripe_event_id: str | None = None,
) -> Subscription | None:
    """Real gap fix: the PAST_DUE/grace-period state machine above
    (_apply_payment_event) already existed, fully correct, but nothing
    real ever fed it - only handle_zoikonex_payment_webhook could trigger
    it, and that's for a ZoikoNex integration that doesn't exist yet.
    Meanwhile create_subscription_checkout_session's mode="subscription"
    Checkout makes STRIPE ITSELF the real, live, auto-recurring biller the
    moment it completes - Stripe charges the customer's card every period
    on its own, independent of ZoikoNex entirely. Confirmed live: a real
    customer whose card failed on a genuine Stripe renewal charge stayed
    ACTIVE with full access forever, since nothing was listening for
    Stripe's own invoice.payment_failed/invoice.paid webhooks. Routed here
    from billing/routes.py's stripe_checkout_webhook for those two event
    types; looks the subscription up by stripe_subscription_id (set once
    by handle_stripe_checkout_completed when the original Checkout
    completed - see that field's docstring on Subscription). Returns None
    if no local Subscription references this Stripe subscription (e.g. one
    this app didn't create)."""
    sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == stripe_subscription_id).first()
    if sub is None:
        return None
    notif_idempotency_key = f"stripe:{stripe_event_id}:{event_type}" if stripe_event_id else None
    return _apply_payment_event(
        db, sub, event_type, actor="stripe_checkout_webhook",
        action="subscription.payment_event_received_stripe", notif_idempotency_key=notif_idempotency_key,
    )


def handle_stripe_subscription_deleted_webhook(db: Session, *, stripe_subscription_id: str) -> Subscription | None:
    """Real gap fix, same rationale as handle_stripe_subscription_payment_
    webhook above: customer.subscription.deleted fires when Stripe cancels
    the subscription on its own side (Smart Retries exhausted after
    repeated failed renewals, or a cancellation made directly in Stripe's
    dashboard/customer portal) - a path that bypasses this app's own POST
    /subscription/cancel entirely. Mirrors cancel_subscription's state
    transition (CANCELED, no grace period), just actor-attributed to
    Stripe instead of the customer. Returns None (no-op) if no local
    Subscription references this Stripe subscription, or it's already
    CANCELED (Stripe can redeliver this webhook)."""
    sub = db.query(Subscription).filter(Subscription.stripe_subscription_id == stripe_subscription_id).first()
    if sub is None or sub.status == SubscriptionStatus.CANCELED:
        return sub
    sub.status = SubscriptionStatus.CANCELED
    sub.canceled_at = _db_now(db)
    db.commit()
    db.refresh(sub)
    sync_subscription_to_zoikonex(db, sub)
    log_event(
        db, actor="stripe_checkout_webhook", action="billing.subscription_canceled_by_stripe",
        target=f"subscription:{sub.id}",
    )
    publish_subscription_canceled(sub.account_id, subscription_id=sub.id, reason="stripe_subscription_deleted")
    return sub


def _is_billing_suspended(sub: Subscription, now: datetime) -> bool:
    """Shared by assert_billing_not_suspended (raises) and get_usage_summary
    (reports) so the two can never disagree on what "suspended" means."""
    if sub.status == SubscriptionStatus.CANCELED:
        return True
    if sub.status != SubscriptionStatus.PAST_DUE or sub.grace_period_ends_at is None:
        return False
    return now > sub.grace_period_ends_at


def assert_billing_not_suspended(db: Session, account_id: str) -> None:
    """Gates outbound calling, video room creation, number purchases, and AI
    summary generation - deliberately NOT called for inbound calls or
    anything that would strand an existing number, per the graceful-
    degradation policy's explicit carve-out (Architecture doc §9).

    CANCELED blocks immediately, no grace period - unlike PAST_DUE (an
    involuntary payment failure the account didn't choose), cancellation is
    a voluntary "stop billing/using me" decision, so there's nothing to
    wait out."""
    sub = get_or_create_subscription(db, account_id)
    if sub.status == SubscriptionStatus.CANCELED:
        raise BillingSuspendedError(
            "This account's subscription has been canceled - resubscribe (change plan) to resume "
            "outbound calling, video, purchases, and AI features."
        )
    if sub.status != SubscriptionStatus.PAST_DUE or sub.grace_period_ends_at is None:
        return
    now = _db_now(db)
    if now > sub.grace_period_ends_at:
        raise BillingSuspendedError(
            "This account's payment is past due and its grace period has ended - "
            "resolve billing to resume outbound calling, video, purchases, and AI features."
        )


class SubscriptionAlreadyCanceledError(Exception):
    """Raised by cancel_subscription when the subscription is already
    CANCELED - idempotency guard, not a retryable error."""


def cancel_subscription(db: Session, account_id: str, *, actor: str, reason: str | None = None) -> Subscription:
    """Customer self-service cancellation (Owner/Admin, same authorization
    bar as change_plan above) - deliberately NOT one of the 4 staff maker-
    checker money-moving actions elsewhere in this file, since this is the
    account's own decision about its own subscription, not a staff-
    initiated action against someone else's money.

    Immediate, not "at period end" - no business decision has been made
    to support a deferred cancellation, and pretending to support one
    without actually deferring anything would be the "invented precision"
    P0-1 already exists to avoid elsewhere. Stops future billing cycles
    (see run_billing_cycle's early-return) and blocks outbound calling/
    video/purchases/AI (see assert_billing_not_suspended) immediately.
    Does NOT touch any owned phone numbers - those already have their own
    per-number cancel path (POST /numbers/{e164}/cancel); cascading this
    into a bulk number release is a separate product decision, not made
    here.

    If this account ever completed a real paid Checkout, Stripe is running
    its own independent recurring charge against a live Subscription object
    (see create_subscription_checkout_session) that nothing else in this
    codebase ever tells to stop. That real subscription is canceled FIRST,
    before any local state changes - if Stripe's cancel fails, this raises
    and nothing is marked canceled here either, so the customer isn't shown
    "canceled" while Stripe silently keeps charging their card."""
    sub = get_or_create_subscription(db, account_id)
    if sub.status == SubscriptionStatus.CANCELED:
        raise SubscriptionAlreadyCanceledError(f"Subscription for account {account_id} is already canceled")

    if sub.stripe_subscription_id:
        from app.integrations.billing import stripe_checkout

        stripe_checkout.cancel_subscription(sub.stripe_subscription_id)

    sub.status = SubscriptionStatus.CANCELED
    sub.canceled_at = _db_now(db)
    db.commit()
    db.refresh(sub)
    sync_subscription_to_zoikonex(db, sub)
    log_event(
        db, actor=actor, action="billing.subscription_canceled", target=f"subscription:{sub.id}",
        after={"reason": reason},
    )
    publish_subscription_canceled(account_id, subscription_id=sub.id, reason=reason)
    return sub


class SubscriptionAlreadyTerminatedError(Exception):
    """Raised by terminate_subscription when the subscription is already TERMINATED."""


class SubscriptionNotEligibleForTerminationError(Exception):
    """Raised when terminating a subscription that's still TRIALING/ACTIVE -
    termination is a deliberate follow-up to cancellation or unresolved
    non-payment, not a way to skip past cancel_subscription."""


def terminate_subscription(db: Session, account_id: str, *, actor: str, reason: str | None = None) -> dict:
    """Commercial Billing Operating Standard doc §M3 - the terminal state
    distinct from CANCELED/PAST_DUE (see SubscriptionStatus.TERMINATED's
    docstring): a final ZoikoNex sync, real provider deprovisioning of
    every owned number, and an irreversible status - unlike CANCELED,
    nothing resubscribes a TERMINATED account back to life.

    Staged through the same maker-checker BillingActionRequest flow as
    credit notes/debit notes/refunds/run_billing_cycle (see
    _execute_billing_action) - a different staff member must approve this
    than the one who requested it, the same segregation-of-duties bar as
    every other sensitive billing action in this file.

    Only reachable from CANCELED or PAST_DUE - an ACTIVE/TRIALING account
    must be cancelled first (see SubscriptionNotEligibleForTerminationError),
    same "cancel first, terminate as a deliberate follow-up" ordering the
    doc describes."""
    sub = get_or_create_subscription(db, account_id)
    if sub.status == SubscriptionStatus.TERMINATED:
        raise SubscriptionAlreadyTerminatedError(f"Subscription for account {account_id} is already terminated")
    if sub.status not in (SubscriptionStatus.CANCELED, SubscriptionStatus.PAST_DUE):
        raise SubscriptionNotEligibleForTerminationError(
            f"Subscription for account {account_id} must be canceled or past due before it can be terminated "
            f"(currently {sub.status.value})"
        )

    before_status = sub.status
    sub.status = SubscriptionStatus.TERMINATED
    sub.terminated_at = _db_now(db)
    db.commit()
    db.refresh(sub)

    # Best-effort, same posture as every other sync_subscription_to_zoikonex
    # call site - an outage here must never leave the account stuck
    # mid-termination (numbers already released below regardless).
    sync_subscription_to_zoikonex(db, sub)

    # Deferred import - app.numbering.numbers.service already imports this
    # module (assert_number_quota_available etc.), so a module-level import
    # here would be circular.
    from app.numbering.numbers.service import release_numbers_for_account_by_system

    released = release_numbers_for_account_by_system(db, account_id, actor=actor, reason=reason or "subscription terminated")

    log_event(
        db, actor=actor, action="billing.subscription_terminated", target=f"subscription:{sub.id}",
        before={"status": before_status.value},
        after={"status": sub.status.value, "reason": reason, "numbers_released": len(released)},
    )
    publish_subscription_terminated(
        account_id, subscription_id=sub.id, reason=reason, numbers_released=len(released),
    )

    from app.numbering.identity.models import User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        plan = get_plan(db, sub.plan_code)
        notify_subscription_terminated(db, account_id=account_id, account_email=owner.email, plan_name=plan.name)

    return {"terminated": True, "subscription_id": sub.id, "numbers_released": len(released)}


def list_zoikonex_sync_events(db: Session, *, account_id: str | None = None, limit: int = 200) -> list[ZoikoNexSyncEvent]:
    query = db.query(ZoikoNexSyncEvent)
    if account_id:
        query = query.filter(ZoikoNexSyncEvent.account_id == account_id)
    return query.order_by(ZoikoNexSyncEvent.created_at.desc()).limit(limit).all()


# Production Readiness & Go-Live Decision Standard §9 acceptance chain -
# "customer portal balance" had nothing customer-facing to show: sync-log
# above is staff-only and accepts an arbitrary account_id. Scoped to the
# billing-meaningful event types only (invoices, captured payments, credit/
# debit notes, refunds) - SUBSCRIPTION_SYNC/USAGE_SYNC/PAYMENT_EVENT_RECEIVED
# are internal plumbing a customer has no reason to see.
CUSTOMER_VISIBLE_SYNC_EVENT_TYPES = (
    ZoikoNexSyncEventType.INVOICE_GENERATED,
    ZoikoNexSyncEventType.PAYMENT_COLLECTED,
    ZoikoNexSyncEventType.CREDIT_NOTE_ISSUED,
    ZoikoNexSyncEventType.DEBIT_NOTE_ISSUED,
    ZoikoNexSyncEventType.REFUND_ISSUED,
)


def list_account_billing_history(db: Session, account_id: str, *, limit: int = 100) -> list[dict]:
    """Returns curated dicts (CustomerBillingHistoryEntryResponse's shape),
    NOT raw ZoikoNexSyncEvent rows - see that schema's docstring on why the
    raw payload must never reach a customer. Only these specific fields are
    ever pulled out of payload; every other key (including anything with
    'error' in it, or the placeholder-price flag) is dropped by omission,
    an allow-list rather than a block-list on purpose."""
    events = (
        db.query(ZoikoNexSyncEvent)
        .filter(
            ZoikoNexSyncEvent.account_id == account_id,
            ZoikoNexSyncEvent.event_type.in_(CUSTOMER_VISIBLE_SYNC_EVENT_TYPES),
        )
        .order_by(ZoikoNexSyncEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "event_type": e.event_type.value,
            "reference": e.zoikonex_ref,
            "amount_minor_units": e.payload.get("amount_minor_units"),
            "status": e.payload.get("invoice_status") or e.payload.get("payment_status") or e.payload.get("status"),
            "reason": e.payload.get("reason"),
            "created_at": e.created_at,
        }
        for e in events
    ]


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
        ZoikoNexReconciliationExceptionType.PAYMENT_AUTHORISED_NOT_CAPTURED: set(),
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

    # Fourth leg: payments authorised by ZoikoNex but never captured
    # because of ZoikoNex's own confirmed capture-side bug (see
    # zoikonex_adapter.capture_payment_intent's docstring). Before this,
    # "authorised but not captured" was discoverable only by hand-querying
    # ZoikoNexSyncEvent payloads - it never entered the operations queue
    # this reconciliation job otherwise puts every other kind of drift into.
    uncaptured_events = (
        db.query(ZoikoNexSyncEvent)
        .filter(
            ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.PAYMENT_COLLECTED,
            ZoikoNexSyncEvent.payload["captured"].astext == "false",
        )
        .all()
    )
    for sync_event in uncaptured_events:
        run.uncaptured_payments_found += 1
        if sync_event.id in already_open[ZoikoNexReconciliationExceptionType.PAYMENT_AUTHORISED_NOT_CAPTURED]:
            continue
        new_exceptions.append(
            ZoikoNexReconciliationException(
                run_id=run.id,
                account_id=sync_event.account_id,
                exception_type=ZoikoNexReconciliationExceptionType.PAYMENT_AUTHORISED_NOT_CAPTURED,
                subject_id=sync_event.id,
                detail=(
                    f"Payment intent {sync_event.zoikonex_ref} was authorised but capture failed: "
                    f"{sync_event.payload.get('capture_error')}"
                ),
            )
        )

    run.exceptions_found = len(new_exceptions)
    for exc in new_exceptions:
        db.add(exc)
    db.commit()
    db.refresh(run)

    if run.exceptions_found > 0:
        # Email Communications System doc's BILL-INT-001 - previously a
        # reconciliation run finding real drift had zero staff-facing
        # signal beyond someone manually checking the reconciliation
        # dashboard; a failed send here must never fail the reconciliation
        # run itself (see send_internal_alert's own try/except per
        # recipient).
        send_internal_alert(
            db, event_name="bill_int.reconciliation_exception",
            summary=(
                f"Reconciliation run {run.id} found {run.exceptions_found} new exception(s): "
                f"{run.unsynced_subscriptions} unsynced subscriptions, {run.unsynced_usage_events} unsynced "
                f"usage events, {run.unmatched_completed_calls} unmatched completed calls, "
                f"{run.late_usage_events} late usage events."
            ),
            console_link=f"{settings.public_base_url}/staff/billing",
            transaction_reference=run.id,
        )
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
        if call.account_id:
            # Deferred import - app.media.service imports this module
            # (billing_service.assert_billing_not_suspended), so a
            # module-level import here would be circular.
            from app.media.service import _invalidate_calls_cache

            _invalidate_calls_cache(call.account_id)
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


def record_pending_number_charge(
    db: Session, account_id: str, *, charge_type: str, phone_number_id: str | None,
    description: str, amount_minor_units: int, currency_code: str,
) -> PendingAccountCharge:
    """Architecture doc §9: a number purchase is an entitlement event that
    should become a line item on the account's next real ZoikoNex invoice,
    not a separate charge on a different rail. Called synchronously at
    number-purchase time (app.numbering.numbers.service.
    create_number_purchase_checkout_session) - the number is provisioned
    immediately either way; this just records what it owes so
    run_billing_cycle's next run picks it up (see the pending-charge loop
    in that function, right after the plan-fee line item)."""
    charge = PendingAccountCharge(
        account_id=account_id, charge_type=charge_type, phone_number_id=phone_number_id,
        description=description, amount_minor_units=amount_minor_units, currency_code=currency_code,
    )
    db.add(charge)
    db.commit()
    return charge


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
    if sub.status == SubscriptionStatus.CANCELED:
        # Not assert_billing_not_suspended - that also blocks PAST_DUE,
        # which must stay billable here so a delinquent account can still
        # be re-billed to attempt collection. CANCELED is the only status
        # with nothing left to ever bill again.
        return {"billed": False, "reason": "subscription is canceled"}
    plan = get_plan(db, sub.plan_code)
    catalog_entry = get_active_price_catalog_entry(db, plan.plan_code, billing_period=sub.billing_period)

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
        if catalog_entry.status != CatalogEntryStatus.ACTIVE:
            raise ZoikoNexBillingCycleError(
                f"Cannot bill plan {plan.plan_code!r} outside development: its price catalog entry "
                f"({catalog_entry.catalog_version}) is {catalog_entry.status.value!r}, not ACTIVE - "
                f"see activate_price_catalog_entry."
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

    zoikonex_adapter.register_plan_in_catalog(
        db, plan, amount_minor_units=amount_minor_units, currency_code=currency_code,
        billing_period=sub.billing_period.value.upper(),
    )

    bill_cycle = zoikonex_adapter.open_bill_cycle(sub)
    invoice = zoikonex_adapter.create_invoice(sub, bill_cycle["bill_cycle_id"], currency_code=currency_code)
    # create_invoice's Idempotency-Key is deterministic per (sub.id,
    # current_period_start), so a retry of an already-issued invoice
    # replays the SAME invoice_id - but confirmed live, that replayed
    # response body is frozen at first-creation time (always "DRAFT"),
    # even after the invoice has since been issued. get_invoice does a
    # live read instead of trusting create_invoice's own return value here.
    from app.numbering.identity.models import User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()

    live_invoice = zoikonex_adapter.get_invoice(invoice["invoice_id"])
    # Gates BOTH notifications below, not just invoice issuance - a
    # customer must get exactly one "invoice issued"/"payment received"
    # pair per billing period, not a fresh "payment received" email every
    # time staff re-runs/retries an already-billed period (ZoikoNex's
    # payment-intent creation is itself idempotent per period, so a re-run
    # was already silently a no-op charge-wise; it just wasn't a no-op
    # notification-wise until this flag was added).
    is_first_run_for_period = live_invoice["status"] == "DRAFT"
    if is_first_run_for_period:
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
            description=f"{plan.name} - {sub.billing_period.value} subscription{description_suffix}",
            amount_minor_units=amount_minor_units,
            tax_amount_minor_units=tax.get("tax_amount_minor_units"),
            line_key="plan-fee",
        )
        line_item_total_minor_units = amount_minor_units
        tax_total_minor_units = tax.get("tax_amount_minor_units") or 0

        # Architecture doc §9: a number purchase is an entitlement event
        # that becomes a line item on THIS SAME invoice, not a separate
        # charge - see record_pending_number_charge. Every row here was
        # accrued since the last time this branch ran (status == PENDING
        # already means "never invoiced"), so no date filtering is needed.
        pending_charges = (
            db.query(PendingAccountCharge)
            .filter(
                PendingAccountCharge.account_id == account_id,
                PendingAccountCharge.status == PendingAccountChargeStatus.PENDING,
            )
            .order_by(PendingAccountCharge.created_at.asc())
            .all()
        )
        for charge in pending_charges:
            charge_tax = zoikonex_adapter.determine_tax_for_invoice_line(
                invoice_id=invoice["invoice_id"], taxable_amount_minor_units=charge.amount_minor_units,
                currency_code=charge.currency_code,
            )
            line_item = zoikonex_adapter.add_invoice_line_item(
                invoice["invoice_id"], description=charge.description,
                amount_minor_units=charge.amount_minor_units,
                tax_amount_minor_units=charge_tax.get("tax_amount_minor_units"),
                line_key=f"pending-charge-{charge.id}",
            )
            charge.status = PendingAccountChargeStatus.INVOICED
            charge.invoiced_at = datetime.now(timezone.utc)
            charge.zoikonex_invoice_id = invoice["invoice_id"]
            charge.zoikonex_line_item_id = line_item["line_item_id"]
            line_item_total_minor_units += charge.amount_minor_units
            tax_total_minor_units += charge_tax.get("tax_amount_minor_units") or 0

        # Pricing doc §5.3 "$29.00 per workspace/month" AI Receptionist
        # add-on + overage - real gap fix: this was metered
        # (usage.service.record_usage_event's ai_receptionist_minutes
        # event) but never actually invoiced. Shares the exact same
        # included-allowance + overage math as get_usage_summary (see
        # _compute_ai_receptionist_overage) so the informational number a
        # customer sees there and the number they're actually charged
        # here can never independently drift. Same placeholder/ACTIVE
        # gate outside development as the plan-fee catalog entry above -
        # a second money-charging price deserves the same "no ad-hoc
        # invented price" discipline, not a quieter exception to it.
        if sub.ai_receptionist_addon_enabled:
            from app.usage.models import UsageEvent

            addon_rate = get_active_ai_receptionist_addon_rate(db)
            if addon_rate is not None and (
                settings.environment == "development"
                or (not addon_rate.is_placeholder and addon_rate.status == CatalogEntryStatus.ACTIVE)
            ):
                addon_minutes_used = float(
                    db.query(sa.func.coalesce(sa.func.sum(UsageEvent.quantity), 0))
                    .filter(
                        UsageEvent.account_id == account_id, UsageEvent.event_type == "ai_receptionist_minutes",
                        UsageEvent.created_at >= sub.current_period_start,
                    )
                    .scalar()
                )
                overage_minutes, overage_cost_cents, _ = _compute_ai_receptionist_overage(
                    db, sub, plan, addon_minutes_used
                )
                addon_tax = zoikonex_adapter.determine_tax_for_invoice_line(
                    invoice_id=invoice["invoice_id"], taxable_amount_minor_units=addon_rate.monthly_price_minor_units,
                    currency_code=addon_rate.currency_code,
                )
                zoikonex_adapter.add_invoice_line_item(
                    invoice["invoice_id"], description="AI Receptionist add-on - monthly",
                    amount_minor_units=addon_rate.monthly_price_minor_units,
                    tax_amount_minor_units=addon_tax.get("tax_amount_minor_units"),
                    line_key="ai-receptionist-addon-fee",
                )
                line_item_total_minor_units += addon_rate.monthly_price_minor_units
                tax_total_minor_units += addon_tax.get("tax_amount_minor_units") or 0

                if overage_cost_cents:
                    overage_tax = zoikonex_adapter.determine_tax_for_invoice_line(
                        invoice_id=invoice["invoice_id"], taxable_amount_minor_units=overage_cost_cents,
                        currency_code=addon_rate.currency_code,
                    )
                    zoikonex_adapter.add_invoice_line_item(
                        invoice["invoice_id"],
                        description=f"AI Receptionist overage - {overage_minutes:.1f} min",
                        amount_minor_units=overage_cost_cents,
                        tax_amount_minor_units=overage_tax.get("tax_amount_minor_units"),
                        line_key="ai-receptionist-overage-fee",
                    )
                    line_item_total_minor_units += overage_cost_cents
                    tax_total_minor_units += overage_tax.get("tax_amount_minor_units") or 0

        issued = zoikonex_adapter.issue_invoice(invoice["invoice_id"])
        # ZoikoNex's own invoice total is authoritative (it's the system of
        # record for invoicing per doc §9) - falls back to the hand-summed
        # Python total only if that field is ever missing, so this never
        # regresses today's behavior.
        payment_amount_minor_units = issued.get("total_minor_units") or line_item_total_minor_units

        if owner is not None:
            tax_amount_minor_units = tax_total_minor_units
            try:
                # A notification failure here must not abort the function:
                # the invoice is already issued at ZoikoNex (a real,
                # non-retriable external effect per create_invoice's own
                # docstring above), and this branch only ever runs once per
                # period (ISSUED invoices are immutable) - if this raised
                # uncaught, a retry would see live_invoice["status"] ==
                # "ISSUED" next time and skip this whole branch forever,
                # permanently losing the receipt even though the billing
                # cycle itself completes successfully on retry.
                notify_invoice_available(
                    db, account_id=account_id, account_email=owner.email,
                    invoice_reference=invoice["invoice_id"],
                    billing_period=f"{sub.current_period_start.date()} to {sub.current_period_end.date()}",
                    subtotal=f"{line_item_total_minor_units / 100:.2f}",
                    tax=f"{tax_amount_minor_units / 100:.2f}",
                    total=f"{(line_item_total_minor_units + tax_amount_minor_units) / 100:.2f}",
                    currency=currency_code,
                )
            except Exception:
                pass
    else:
        issued = {"status": live_invoice["status"]}
        payment_amount_minor_units = live_invoice.get("total_minor_units") or amount_minor_units
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
        sub, invoice["invoice_id"], amount_minor_units=payment_amount_minor_units, currency_code=currency_code,
    )
    zoikonex_adapter.authorise_payment_intent(intent["payment_intent_id"])

    result = {
        "billed": True, "plan_code": plan.plan_code, "amount_minor_units": payment_amount_minor_units,
        "invoice_id": invoice["invoice_id"], "payment_intent_id": intent["payment_intent_id"],
        "invoice_status": issued["status"], "payment_status": "authorised", "captured": False,
        "capture_error": None, "bill_cycle_closed": bill_cycle_closed, "bill_cycle_close_error": bill_cycle_close_error,
    }
    try:
        zoikonex_adapter.capture_payment_intent(intent["payment_intent_id"])
        result["captured"] = True
        result["payment_status"] = "captured"

        # Gated on is_first_run_for_period (not just "capture succeeded")
        # so a re-run/retry of an already-billed period - ZoikoNex's own
        # payment-intent creation is idempotent per period, so this branch
        # can genuinely execute again for the same charge - doesn't send a
        # second, spurious "payment received" email for money that was
        # never actually charged twice.
        if owner is not None and is_first_run_for_period:
            try:
                notify_payment_succeeded(
                    db, account_id=account_id, account_email=owner.email,
                    total=f"{amount_minor_units / 100:.2f}", currency=currency_code,
                    description=f"{plan.name} plan - {sub.billing_period.value} subscription",
                    payment_date=_db_now(db).date().isoformat(),
                    payment_method_masked="on file with ZoikoNex",
                )
            except Exception:
                pass
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


def get_active_ai_receptionist_addon_rate(db: Session) -> AIReceptionistAddonRate | None:
    """Mirrors get_active_price_catalog_entry's "prefer ACTIVE, fall back to
    most-recently-created" dev convenience - there's only ever meant to be
    one rate in flight at a time (no per-market variation in the doc for
    this add-on), so no market/plan_code filter is needed here."""
    active = (
        db.query(AIReceptionistAddonRate)
        .filter(AIReceptionistAddonRate.status == CatalogEntryStatus.ACTIVE)
        .order_by(AIReceptionistAddonRate.created_at.desc())
        .first()
    )
    if active is not None:
        return active
    return db.query(AIReceptionistAddonRate).order_by(AIReceptionistAddonRate.created_at.desc()).first()


def is_ai_receptionist_enabled_for_account(db: Session, account_id: str) -> bool:
    """Single source of truth for "does this account's plan/add-on grant AI
    Receptionist at all" - previously every caller that needed this
    combined plan.included_ai_receptionist_minutes and
    sub.ai_receptionist_addon_enabled itself (get_usage_summary did; the
    per-number ai_receptionist_enabled toggle in configure_routing didn't
    check either at all). Reused by configure_routing's entitlement gate
    and get_usage_summary's snapshot below."""
    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)
    return plan.included_ai_receptionist_minutes > 0 or sub.ai_receptionist_addon_enabled


def _compute_ai_receptionist_overage(
    db: Session, sub: Subscription, plan: Plan, ai_receptionist_minutes_used: float,
) -> tuple[float, int | None, AIReceptionistAddonRate | None]:
    """Pricing doc §5.3 included-allowance + overage math - single source
    of truth shared by get_usage_summary (informational) and
    run_billing_cycle (the real charge), so the two can never
    independently drift apart the way ai_receptionist_minutes' usage-
    event write once did (see media.service.update_call_status's fix
    history). Plan-granted minutes (Pro/Scale) and add-on-granted minutes
    (Starter/Business who bought the $29/mo add-on) stack."""
    addon_rate = get_active_ai_receptionist_addon_rate(db) if sub.ai_receptionist_addon_enabled else None
    minutes_included = plan.included_ai_receptionist_minutes + (
        addon_rate.included_minutes if addon_rate is not None else 0
    )
    overage_minutes = max(0.0, ai_receptionist_minutes_used - minutes_included)
    overage_cost_cents = None
    if overage_minutes > 0:
        overage_rate = addon_rate or get_active_ai_receptionist_addon_rate(db)
        if overage_rate is not None:
            overage_cost_cents = round(overage_minutes * overage_rate.overage_rate_minor_units_per_minute)
    return overage_minutes, overage_cost_cents, addon_rate


def set_ai_receptionist_addon(db: Session, account_id: str, *, enabled: bool, actor: str) -> Subscription:
    """Pricing doc §5.3 "$29.00 per workspace/month" - a subscription-level
    toggle, not a plan change (see change_plan for that), so this doesn't
    touch plan_code or re-run entitlement checks. Audited the same way
    every other billing state change in this module is (see
    change_plan/cancel_subscription) - this is real money-adjacent state,
    not a UI preference."""
    sub = get_or_create_subscription(db, account_id)
    previous = sub.ai_receptionist_addon_enabled
    sub.ai_receptionist_addon_enabled = enabled
    db.commit()
    db.refresh(sub)
    log_event(
        db, actor_id=actor, action="billing.ai_receptionist_addon_changed",
        target_type="subscription", target_id=sub.id,
        metadata={"account_id": account_id, "previous": previous, "enabled": enabled},
    )
    return sub


def get_usage_summary(db: Session, account_id: str) -> dict:
    """Compares this billing period's real usage against the account's plan
    limits. Voice minutes come from real UsageEvent rows (call_seconds,
    populated since day one of usage metering); video minutes and AI
    summaries are also real UsageEvent rows as of this feature (see
    app.media.service / app.intelligence.service call sites) - informational
    only, not hard-enforced (unlike number/seat counts), since blocking an
    in-progress call or AI generation on a usage cap is a materially bigger
    and riskier change than gating a discrete purchase/invite action."""
    from app.numbering.identity.models import User
    from app.usage.models import UsageEvent
    from sqlalchemy import func as sa_func

    sub = get_or_create_subscription(db, account_id)
    plan = get_plan(db, sub.plan_code)
    now = _db_now(db)
    seat_count = db.query(User).filter(User.account_id == account_id).count()

    totals = dict(
        db.query(UsageEvent.event_type, sa_func.coalesce(sa_func.sum(UsageEvent.quantity), 0))
        .filter(UsageEvent.account_id == account_id, UsageEvent.created_at >= sub.current_period_start)
        .group_by(UsageEvent.event_type)
        .all()
    )

    voice_minutes_used = float(totals.get("call_seconds", 0)) / 60
    video_minutes_used = float(totals.get("video_participant_minutes", 0))
    ai_summaries_used = float(totals.get("ai_summary", 0))
    ai_receptionist_minutes_used = float(totals.get("ai_receptionist_minutes", 0))

    # Pricing doc §5.3 included-allowance + overage math (see
    # _compute_ai_receptionist_overage, shared with run_billing_cycle -
    # which DOES now actually charge this, unlike every other resource
    # reported here).
    ai_receptionist_overage_minutes, ai_receptionist_overage_cost_cents, addon_rate = (
        _compute_ai_receptionist_overage(db, sub, plan, ai_receptionist_minutes_used)
    )
    ai_receptionist_minutes_included = plan.included_ai_receptionist_minutes + (
        addon_rate.included_minutes if addon_rate is not None else 0
    )

    return {
        "plan_code": plan.plan_code,
        "plan_name": plan.name,
        "status": sub.status.value,
        "trial_ends_at": sub.trial_ends_at,
        "current_period_start": sub.current_period_start,
        "current_period_end": sub.current_period_end,
        "ai_receptionist_addon_enabled": sub.ai_receptionist_addon_enabled,
        # Entitlement-snapshot fields (Commercial Entitlement Governance doc) -
        # single source of truth shared with assert_billing_not_suspended /
        # is_ai_receptionist_enabled_for_account so this can never disagree
        # with what actually gets blocked elsewhere.
        "is_suspended": _is_billing_suspended(sub, now),
        "ai_receptionist_enabled": plan.included_ai_receptionist_minutes > 0 or sub.ai_receptionist_addon_enabled,
        "resources": [
            {"resource": "voice_minutes", "used": round(voice_minutes_used, 1), "limit": plan.monthly_voice_minutes},
            {"resource": "video_minutes", "used": round(video_minutes_used, 1), "limit": plan.monthly_video_minutes},
            {"resource": "ai_summaries", "used": int(ai_summaries_used), "limit": plan.monthly_ai_summaries},
            {
                "resource": "numbers",
                "used": _count_owned_or_in_flight_numbers(db, account_id),
                "limit": plan.max_numbers,
            },
            {"resource": "seats", "used": seat_count, "limit": plan.max_team_seats},
            {
                "resource": "ai_receptionist_minutes",
                "used": round(ai_receptionist_minutes_used, 1),
                "limit": ai_receptionist_minutes_included,
                "overage_minutes": round(ai_receptionist_overage_minutes, 1),
                "estimated_overage_cost_cents": ai_receptionist_overage_cost_cents,
            },
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
    if request.action_type == BillingActionType.TERMINATE_SUBSCRIPTION:
        return terminate_subscription(db, payload["account_id"], reason=payload.get("reason"), actor=actor)
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
