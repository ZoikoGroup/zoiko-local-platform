from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.service import sync_usage_event_to_zoikonex
from app.events.service import publish_usage_rated
from app.usage.models import (
    DEFAULT_RATE_COUNTRY,
    AIUsageRate,
    CallingRate,
    NumberRate,
    UsageAdjustment,
    UsageDispute,
    UsageDisputeStatus,
    UsageEvent,
)

# Commercial Billing Operating Standard doc §E1/§30 - bumped only when the
# rounding/minimum-increment/disposition-billability rule below actually
# changes, so historical UsageEvent rows stay traceable to the rule that
# rated them (see UsageEvent.meter_version's docstring).
CURRENT_METER_VERSION = "v1"

# §E5 - a call that actually connected still bills at least this many
# seconds even if the provider reports 0 (e.g. connected and dropped inside
# the same second). Doesn't apply to non-duration event types (video
# minutes, AI summaries), which pass disposition=None and skip this floor.
MINIMUM_BILLABLE_SECONDS = 1

# §E2 - dispositions that mean "the call never connected" are not
# retail-billable by default, even though the provider may have incurred a
# real wholesale cost. Only meaningful for event types that pass a
# disposition at all (currently just "call_seconds").
NON_BILLABLE_DISPOSITIONS = {"failed", "busy", "no-answer", "canceled"}


def get_calling_rate(db: Session, country: str | None) -> CallingRate | None:
    """country-specific rate if one's configured, else the DEFAULT_RATE_
    COUNTRY fallback, else None (no rate configured at all yet)."""
    if country is not None:
        rate = db.query(CallingRate).filter(CallingRate.country == country).first()
        if rate is not None:
            return rate
    return db.query(CallingRate).filter(CallingRate.country == DEFAULT_RATE_COUNTRY).first()


def list_calling_rates(db: Session) -> list[CallingRate]:
    return db.query(CallingRate).order_by(CallingRate.country.asc()).all()


def upsert_calling_rate(db: Session, *, country: str, price_per_minute_cents: int, currency: str = "USD") -> CallingRate:
    rate = db.query(CallingRate).filter(CallingRate.country == country).first()
    if rate is None:
        rate = CallingRate(country=country, price_per_minute_cents=price_per_minute_cents, currency=currency)
        db.add(rate)
    else:
        rate.price_per_minute_cents = price_per_minute_cents
        rate.currency = currency
    db.commit()
    db.refresh(rate)
    return rate


def list_number_rates(db: Session) -> list[NumberRate]:
    return db.query(NumberRate).order_by(NumberRate.country.asc(), NumberRate.number_type.asc()).all()


def get_number_rate(db: Session, country: str | None, number_type: str) -> NumberRate | None:
    """country-specific rate for this number_type if one's configured, else
    the DEFAULT_RATE_COUNTRY fallback, else None - same lookup shape as
    get_calling_rate above."""
    if country is not None:
        rate = (
            db.query(NumberRate)
            .filter(NumberRate.country == country, NumberRate.number_type == number_type)
            .first()
        )
        if rate is not None:
            return rate
    return (
        db.query(NumberRate)
        .filter(NumberRate.country == DEFAULT_RATE_COUNTRY, NumberRate.number_type == number_type)
        .first()
    )


def upsert_number_rate(
    db: Session, *, country: str, number_type: str = "local", recurring_price_cents: int,
    currency: str = "USD", is_placeholder: bool = False,
) -> NumberRate:
    rate = db.query(NumberRate).filter(NumberRate.country == country, NumberRate.number_type == number_type).first()
    if rate is None:
        rate = NumberRate(
            country=country, number_type=number_type, recurring_price_cents=recurring_price_cents,
            currency=currency, is_placeholder=is_placeholder,
        )
        db.add(rate)
    else:
        rate.recurring_price_cents = recurring_price_cents
        rate.currency = currency
        rate.is_placeholder = is_placeholder
    db.commit()
    db.refresh(rate)
    return rate


def get_ai_usage_rate(db: Session) -> AIUsageRate | None:
    """One active row expected (global, not country-specific - see
    AIUsageRate's docstring); takes the most recently created if a price
    revision ever adds a second one. Deliberately NOT the same discipline
    as CallingRate/NumberRate's upserts below (those edit the existing row
    in place) - upsert_ai_usage_rate always inserts a new row instead, so
    a price revision stays reconstructable (which rate was in effect when),
    matching PriceCatalogEntry's "new version, never a silent edit"
    treatment of real approved prices rather than CallingRate/NumberRate's
    placeholder-style single mutable row."""
    return db.query(AIUsageRate).order_by(AIUsageRate.created_at.desc()).first()


def upsert_ai_usage_rate(
    db: Session, *, overage_price_cents_per_minute: int, currency: str = "USD", is_placeholder: bool = False,
) -> AIUsageRate:
    """Always inserts - see get_ai_usage_rate's docstring on why this is
    intentionally NOT an in-place edit like upsert_calling_rate/
    upsert_number_rate below. The $29/workspace/month add-on itself is
    priced separately by billing.service's AIReceptionistAddonRate rows,
    not through this function."""
    rate = AIUsageRate(
        overage_price_cents_per_minute=overage_price_cents_per_minute,
        currency=currency, is_placeholder=is_placeholder,
    )
    db.add(rate)
    db.commit()
    db.refresh(rate)
    return rate


def record_usage_event(
    db: Session,
    *,
    account_id: str,
    event_type: str,
    quantity: float,
    unit: str,
    country_band: str | None,
    idempotency_key: str,
    disposition: str | None = None,
) -> UsageEvent | None:
    """Returns None (no-op) if this exact event was already recorded - a
    provider webhook firing twice for the same call must not double-count
    usage.

    `quantity` is always the raw/measured amount as reported by the caller
    (unchanged contract for callers with no disposition concept, e.g. video
    minutes/AI summaries - they pass disposition=None and get billed exactly
    what they measured, no floor applied). When `disposition` is given
    (currently only call_seconds), the Commercial Billing Operating
    Standard doc's §E2/§E5 rule applies: a disposition in
    NON_BILLABLE_DISPOSITIONS bills 0 regardless of measured duration; a
    connected call bills at least MINIMUM_BILLABLE_SECONDS. The raw
    measurement is preserved in `raw_quantity` either way, per §E6 "never
    destructively replace raw measured duration"."""
    existing = db.query(UsageEvent).filter(UsageEvent.idempotency_key == idempotency_key).first()
    if existing is not None:
        return None

    if disposition is None:
        billed_quantity = quantity
    elif disposition in NON_BILLABLE_DISPOSITIONS:
        billed_quantity = 0.0
    else:
        billed_quantity = max(quantity, MINIMUM_BILLABLE_SECONDS)

    # estimated_cost_cents starts NULL - usage capture must never be
    # blocked on rating, so the actual $ decision is made afterward by
    # ZoikoNex (see sync_usage_event_to_zoikonex/zoikonex_adapter.
    # rate_usage_event) rather than computed here ad hoc.
    event = UsageEvent(
        account_id=account_id,
        event_type=event_type,
        quantity=billed_quantity,
        raw_quantity=quantity,
        disposition=disposition,
        meter_version=CURRENT_METER_VERSION,
        unit=unit,
        country_band=country_band,
        idempotency_key=idempotency_key,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        # lost the race against a concurrent duplicate webhook - the other
        # commit already recorded this event, so this one is correctly a no-op
        db.rollback()
        return None
    db.refresh(event)
    publish_usage_rated(
        account_id, usage_event_id=event.id, event_type=event_type, quantity=billed_quantity,
        unit=unit, country_band=country_band,
    )
    sync_usage_event_to_zoikonex(db, event)
    return event


def list_account_usage(db: Session, account_id: str) -> list[UsageEvent]:
    return (
        db.query(UsageEvent)
        .filter(UsageEvent.account_id == account_id)
        .order_by(UsageEvent.created_at.desc())
        .all()
    )


class UsageEventNotFoundError(Exception):
    """Raised when a usage_event_id doesn't exist (or doesn't belong to
    the account raising a dispute against it)."""


class UsageDisputeNotFoundError(Exception):
    """Raised when a usage_dispute_id doesn't exist."""


class UsageDisputeAlreadyResolvedError(Exception):
    """Raised when trying to resolve a dispute that's already
    RESOLVED_ADJUSTED or RESOLVED_DENIED."""


class InvalidUsageDisputeResolutionError(Exception):
    """Raised for a resolution status other than RESOLVED_ADJUSTED/
    RESOLVED_DENIED, or an ADJUSTED resolution missing the new cost."""


def open_usage_dispute(db: Session, *, account_id: str, usage_event_id: str, reason: str, raised_by: str) -> UsageDispute:
    """Commercial Billing Operating Standard doc §L1/E6 - the customer-
    facing half of the append-only billing-correction trail (see
    UsageDispute's docstring). Any account member can raise one against a
    usage event on their own account; resolving it is staff-only (see
    resolve_usage_dispute)."""
    event = db.query(UsageEvent).filter(UsageEvent.id == usage_event_id, UsageEvent.account_id == account_id).first()
    if event is None:
        raise UsageEventNotFoundError(f"No such usage event {usage_event_id!r} on this account")
    dispute = UsageDispute(account_id=account_id, usage_event_id=usage_event_id, reason=reason, raised_by=raised_by)
    db.add(dispute)
    db.commit()
    db.refresh(dispute)
    log_event(
        db, actor=raised_by, action="usage.dispute_opened", target=f"usage_dispute:{dispute.id}",
        after={"usage_event_id": usage_event_id, "reason": reason},
    )
    return dispute


def list_account_usage_disputes(db: Session, account_id: str) -> list[UsageDispute]:
    return db.query(UsageDispute).filter(UsageDispute.account_id == account_id).order_by(UsageDispute.created_at.desc()).all()


def list_usage_disputes(db: Session, status_filter: UsageDisputeStatus | None = None) -> list[UsageDispute]:
    """Staff-only queue view - any staff role can view (diagnostic, same
    posture as the fraud-case queue); resolving one is the sensitive
    action, gated at the route."""
    query = db.query(UsageDispute)
    if status_filter is not None:
        query = query.filter(UsageDispute.status == status_filter)
    return query.order_by(UsageDispute.created_at.desc()).all()


def create_usage_adjustment(
    db: Session, usage_event_id: str, *, new_estimated_cost_cents: int, reason: str, actor: str,
    dispute_id: str | None = None,
) -> UsageAdjustment:
    """The ONLY code path allowed to change UsageEvent.estimated_cost_cents
    after creation - see UsageAdjustment's docstring on why this must
    never happen as a bare UPDATE elsewhere. Staff-only, gated at the
    route (billing.resolve_usage_dispute - same capability whether or not
    a formal dispute exists, since the sensitivity is "changing a billed
    amount," not "responding to a customer complaint")."""
    event = db.query(UsageEvent).filter(UsageEvent.id == usage_event_id).first()
    if event is None:
        raise UsageEventNotFoundError(f"No such usage event {usage_event_id!r}")
    adjustment = UsageAdjustment(
        usage_event_id=usage_event_id, dispute_id=dispute_id,
        previous_estimated_cost_cents=event.estimated_cost_cents,
        new_estimated_cost_cents=new_estimated_cost_cents, reason=reason, actor=actor,
    )
    event.estimated_cost_cents = new_estimated_cost_cents
    db.add(adjustment)
    db.commit()
    db.refresh(adjustment)
    log_event(
        db, actor=actor, action="usage.adjustment_created", target=f"usage_event:{usage_event_id}",
        before={"estimated_cost_cents": adjustment.previous_estimated_cost_cents},
        after={"estimated_cost_cents": new_estimated_cost_cents, "reason": reason, "dispute_id": dispute_id},
    )
    return adjustment


def resolve_usage_dispute(
    db: Session, dispute_id: str, *, actor: str, status: UsageDisputeStatus, notes: str | None = None,
    new_estimated_cost_cents: int | None = None,
) -> UsageDispute:
    """Staff-only. status must be RESOLVED_ADJUSTED or RESOLVED_DENIED -
    OPEN/INVESTIGATING aren't resolutions, they're the dispute's own
    starting/in-progress states. RESOLVED_ADJUSTED atomically creates the
    UsageAdjustment ledger row (see create_usage_adjustment) in the same
    call, rather than requiring the caller to remember to make two
    separate requests."""
    if status not in (UsageDisputeStatus.RESOLVED_ADJUSTED, UsageDisputeStatus.RESOLVED_DENIED):
        raise InvalidUsageDisputeResolutionError(
            "A dispute can only be resolved as 'resolved_adjusted' or 'resolved_denied'"
        )
    dispute = db.query(UsageDispute).filter(UsageDispute.id == dispute_id).first()
    if dispute is None:
        raise UsageDisputeNotFoundError(f"No such usage dispute {dispute_id!r}")
    if dispute.status in (UsageDisputeStatus.RESOLVED_ADJUSTED, UsageDisputeStatus.RESOLVED_DENIED):
        raise UsageDisputeAlreadyResolvedError(f"Dispute {dispute_id} is already {dispute.status.value}")

    if status == UsageDisputeStatus.RESOLVED_ADJUSTED:
        if new_estimated_cost_cents is None:
            raise InvalidUsageDisputeResolutionError("new_estimated_cost_cents is required to resolve as resolved_adjusted")
        create_usage_adjustment(
            db, dispute.usage_event_id, new_estimated_cost_cents=new_estimated_cost_cents,
            reason=notes or f"Usage dispute {dispute_id} resolved with adjustment", actor=actor, dispute_id=dispute_id,
        )

    dispute.status = status
    dispute.resolved_by = actor
    dispute.resolution_notes = notes
    dispute.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(dispute)
    log_event(
        db, actor=actor, action="usage.dispute_resolved", target=f"usage_dispute:{dispute.id}",
        after={"status": status.value, "notes": notes},
    )
    return dispute
