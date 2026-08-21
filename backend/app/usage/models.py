import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid

# Sentinel country_band used for a rate that applies to any country without
# its own row - see CallingRate/get_calling_rate. Not a real ISO code, so it
# can never collide with an actual country.
DEFAULT_RATE_COUNTRY = "XX"


class CallingRate(Base):
    """Per-country outbound calling price, keyed by the calling number's own
    country (PhoneNumber.country / UsageEvent.country_band) - not the call's
    destination. Destination-based international rating would need a real
    E.164-to-country parser (a new dependency this platform doesn't have),
    so this rates by which of the account's own numbers placed the call,
    the same dimension country_band already captures for billing. A row
    with country=DEFAULT_RATE_COUNTRY is the fallback for any curated
    country without its own explicit rate.

    Placeholder pricing, not a real carrier rate card - there's no live
    telecom billing gateway yet (see purchase_number's docstring on the
    matching gap for number purchase). This only prices UsageEvent rows for
    visibility; it does not charge anyone."""

    __tablename__ = "calling_rates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    country: Mapped[str] = mapped_column(String(2), unique=True, nullable=False, index=True)
    price_per_minute_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NumberRate(Base):
    """Global Plans, Pricing & Commercial Launch Standard doc §5.1: "Additional
    standard local number: From $4.99/month... exact retail price is
    country/number-type specific." Keyed like CallingRate (country +
    number_type, with a DEFAULT_RATE_COUNTRY fallback row) since the doc
    only gives one baseline figure, not a full per-country card yet.

    Same "prices UsageEvent rows for visibility; does not charge anyone"
    posture as CallingRate - there's no live recurring-billing gateway
    (see purchase_number's docstring on the matching one-time-purchase
    gap). Unlike CallingRate's seeded test numbers, the baseline row here
    IS the real, doc-approved figure - is_placeholder distinguishes that
    for any future non-baseline rows added before a real commercial
    decision backs them."""

    __tablename__ = "number_rates"
    __table_args__ = (UniqueConstraint("country", "number_type", name="uq_number_rate_country_type"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    number_type: Mapped[str] = mapped_column(String(30), nullable=False, default="local", server_default="local")
    recurring_price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_placeholder: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIUsageRate(Base):
    """Global Plans, Pricing & Commercial Launch Standard doc §5.3: AI
    Receptionist overage at $0.39/minute after the plan's included
    allowance. Global, not country-specific - modeled as a table (not a
    bare constant) for the same reason CallingRate/NumberRate are: a real
    price revision must be a new auditable row, not a silent constant
    edit. One active row expected; get_ai_usage_rate takes the most
    recently created.

    Same visibility-only posture as CallingRate/NumberRate - prices
    ai_receptionist_minutes UsageEvent rows for display, does not charge
    anyone.

    The $29/workspace/month add-on itself (with its own included-minutes
    allowance and overage rate) is priced separately by
    billing.models.AIReceptionistAddonRate, not by this table - see that
    class's docstring."""

    __tablename__ = "ai_usage_rates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    overage_price_cents_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    is_placeholder: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(Base):
    """Rating input for a future billing integration (Architecture doc §7
    data model: "Usage Event... rating input for ZoikoNex"; Roadmap §2 lists
    "usage metering" as Phase 1 Voice scope in its own right, not contingent
    on ZoikoNex existing). Zoiko Local owns real-time usage capture; nothing
    downstream (ZoikoNex) exists yet to consume it, but the capture itself
    doesn't need to wait on that integration.

    idempotency_key prevents double-counting when a provider webhook (e.g.
    Twilio's status-callback) fires more than once for the same event.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    country_band: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Priced from CallingRate at write time for "call_seconds" events only -
    # NULL for every other event_type (video minutes, AI summaries, etc.),
    # which have no rate table yet. An estimate for visibility, not a real
    # charge - see CallingRate's docstring on the same gap.
    estimated_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Commercial Billing Operating Standard doc §E2 - the provider-reported
    # outcome (e.g. "completed"/"busy"/"no-answer"/"failed" for calls) that
    # justified whether/how this event was billable. NULL for event types
    # with no disposition concept (video minutes, AI summaries) - those are
    # only ever recorded on their own success path already.
    disposition: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # §E5/E6 - the unrounded, unfloored measurement (e.g. raw call seconds)
    # kept alongside `quantity` (the rated/floored figure actually used for
    # estimated_cost_cents) so a rounding/minimum-increment rule is always
    # traceable back to what was actually measured, never silently replacing
    # it. NULL when quantity already IS the raw measurement (no rating rule
    # applies to this event_type yet).
    raw_quantity: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    # §E1/§30 "Identity" - which version of the rating rule (rounding,
    # minimum increment, disposition-billability policy) produced `quantity`
    # from `raw_quantity`, so a future rule change never silently reinterprets
    # historical usage. Bumped only when the rule in record_usage_event
    # actually changes.
    meter_version: Mapped[str] = mapped_column(String(20), nullable=False, server_default="v1")
    # Commercial Billing Operating Standard P0-8 "rating versioning" - a
    # distinct concept from `meter_version` above: this pins which version of
    # the *rate catalog* (CallingRate) priced the event, not which rule
    # rounded/floored it. A fixed literal, not a real versioning system
    # (CallingRate has no history table; upsert_calling_rate mutates in
    # place - see its docstring), same honest "only one version exists so
    # far" posture as ZoikoNex's own catalog_version_id="v1" elsewhere in
    # this codebase. Recorded alongside estimated_cost_cents at rating time
    # (see app.billing.service.sync_usage_event_to_zoikonex) - NULL until
    # rated, same lifecycle as estimated_cost_cents itself, so a query can
    # tell "not yet rated" apart from "rated under an unversioned catalog"
    # (there is no such state once this exists).
    rate_meter_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # P0-8 - stamped alongside estimated_cost_cents at ZoikoNex-sync rating
    # time (see app.billing.service.sync_usage_event_to_zoikonex) - NULL
    # until rated, so a query can tell "not yet rated" apart from "rated a
    # while ago." Distinct from meter_version above, which versions the
    # record_usage_event-time rating RULE, not the ZoikoNex-sync step.
    rated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageDisputeStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED_ADJUSTED = "resolved_adjusted"
    RESOLVED_DENIED = "resolved_denied"


class UsageDispute(Base):
    """Commercial Billing Operating Standard doc §L1/E6 - "billing
    corrections have no structured, append-only audit trail" was the gap:
    before this, a customer disputing a charge had nowhere to raise it and
    staff had no record of the conversation, only whatever ad hoc note
    (if any) ended up in a support ticket outside this codebase. One
    dispute per usage_event_id isn't enforced (a customer could
    conceivably raise a second one after a denial), but resolving one
    requires it be OPEN/INVESTIGATING first - see resolve_usage_dispute."""

    __tablename__ = "usage_disputes"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    usage_event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[UsageDisputeStatus] = mapped_column(
        Enum(UsageDisputeStatus, name="usage_dispute_status_enum"), nullable=False, default=UsageDisputeStatus.OPEN
    )
    raised_by: Mapped[str] = mapped_column(String(100), nullable=False)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageAdjustment(Base):
    """Append-only correction ledger - the actual mechanism §L1/E6 asks
    for. app.usage.service.create_usage_adjustment is the ONLY code path
    allowed to change UsageEvent.estimated_cost_cents after creation, and
    it always writes one of these rows in the same transaction - the
    value is never bare-UPDATEd elsewhere. Rows here are never edited or
    deleted once created (a further correction is a NEW row, same Class A
    discipline as PriceCatalogEntry/ZoikoNex PriceRule), so the full
    history of every change to a usage event's billed cost is always
    reconstructable. dispute_id is nullable - staff can proactively
    correct a metering bug without a customer having raised a dispute
    first."""

    __tablename__ = "usage_adjustments"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    usage_event_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("usage_events.id", ondelete="CASCADE"), nullable=False, index=True
    )
    dispute_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("usage_disputes.id", ondelete="SET NULL"), nullable=True
    )
    previous_estimated_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_estimated_cost_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
