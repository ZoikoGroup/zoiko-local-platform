import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class Plan(Base):
    """Architecture doc §5: "Subscription and Entitlement... Plans, limits,
    feature gates, number allowances, minute/video/AI quotas, grace
    periods" - a Zoiko-owned service, explicitly separate from the
    "ZoikoNex Billing Adapter" row in the same table. Stored as data (not
    hardcoded limits in Python), same discipline as this project's
    compliance rules, so plan limits can be tuned without a deploy.

    No price fields - no payment processing exists here at all (that's
    ZoikoNex's job once the connection is built). This purely gates
    feature/resource usage.
    """

    __tablename__ = "plans"

    plan_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    max_numbers: Mapped[int] = mapped_column(Integer, nullable=False)
    max_team_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_voice_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_video_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_ai_summaries: Mapped[int] = mapped_column(Integer, nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Populated once by app.integrations.billing.zoikonex.register_plan_in_catalog
    # - ZoikoNex's product-catalogue-commercial service is the actual price
    # authority (Product + Offer + PriceRule); NULL until that one-time
    # registration has run for this plan_code.
    zoikonex_product_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zoikonex_offer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zoikonex_price_rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subscription(Base):
    """Architecture doc §7 data model: "Commercial entitlement container
    synced with ZoikoNex" - subscription_id, plan_code, status, period,
    entitlements_snapshot, zoikonex_ref. entitlements_snapshot is
    deliberately NOT stored separately here - Plan IS the entitlements
    snapshot for Phase 1 (a live FK, not a point-in-time copy), since there's
    no billing cycle yet where a snapshot would diverge from the current
    plan definition.

    zoikonex_ref is populated by the MOCK adapter (app.integrations.billing
    .zoikonex) since there is no real ZoikoNex API yet — see that module's
    docstring for why this exists at all despite the event contract never
    being locked. Swapping the mock for a real client later needs no schema
    change here.
    """

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(50), ForeignKey("plans.plan_code"), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum"), nullable=False, default=SubscriptionStatus.TRIALING
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zoikonex_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # ZoikoNex's own Party -> Customer -> Account chain (customer-account
    # service) - a real, separate identity model from ours, created once per
    # account the first time its subscription syncs. zoikonex_pii_token is
    # the opaque reference customer-account requires in place of a plaintext
    # name (ZN-ADR-013, "PII is vaulted") - identity-tenancy's PII vault has
    # no reachable API yet (confirmed against its source: the domain logic
    # exists but no HTTP/gRPC route calls it), so this is a freshly-generated
    # placeholder UUID, not a real vaulted token. Swap for a real vault call
    # the moment ZoikoNex exposes one - no schema change needed here.
    zoikonex_party_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zoikonex_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zoikonex_account_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    zoikonex_pii_token: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Architecture doc §9 "Graceful degradation": set when a (mock) payment
    # failure is received, cleared on restoration. Incoming calls and number
    # ownership stay active regardless; outbound calling/video/purchases/AI
    # are gated once this passes (see app.billing.service.
    # assert_billing_not_suspended) - NULL means "no grace period in effect."
    grace_period_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZoikoNexSyncEventType(str, enum.Enum):
    SUBSCRIPTION_SYNC = "subscription_sync"
    USAGE_SYNC = "usage_sync"
    PAYMENT_EVENT_RECEIVED = "payment_event_received"


class ZoikoNexSyncEvent(Base):
    """Outbound/inbound sync ledger for the mock ZoikoNex adapter - real
    even though the adapter itself is mocked, this is what a reconciliation
    job (Architecture doc §9's "daily reconciliation jobs... exceptions
    enter an operations queue") would actually compare against once a real
    ZoikoNex exists. See app.integrations.billing.zoikonex's docstring."""

    __tablename__ = "zoikonex_sync_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    event_type: Mapped[ZoikoNexSyncEventType] = mapped_column(
        Enum(ZoikoNexSyncEventType, name="zoikonex_sync_event_type_enum"), nullable=False, index=True
    )
    zoikonex_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # ZoikoNex's own event id for inbound webhook deliveries (Architecture doc
    # §9: integration "must be event-based, idempotent, and reconcilable") -
    # NULL for our own outbound sync events, which have no such id to dedupe
    # against. A unique index lets a replayed webhook delivery be detected
    # and skipped instead of double-applying a payment-state transition.
    external_event_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ZoikoNexReconciliationRun(Base):
    """Architecture doc §9: "daily reconciliation jobs compare Zoiko Local
    entitlements and usage events with ZoikoNex invoices, payments, and
    ledger state." Unlike get_zoikonex_reconciliation_summary (a live,
    uncached aggregate count with no history), each run here is persisted -
    same pattern as app.ops.models.SyntheticCheckRun - so staff can see
    whether drift is growing or shrinking over time, not just its current
    value. No scheduler exists in this codebase yet (see
    app.ops.routes.run_synthetic_checks's docstring for the same gap), so
    this is staff-triggered on demand rather than actually running daily.

    total_completed_calls/unmatched_completed_calls are the third,
    carrier-evidence leg the Commercial Billing Operating Standard doc's
    "three-way reconciliation (Zoiko Local <-> ZoikoNex <-> carrier)" asks
    for - CallRecord rows Twilio confirmed completed (real carrier
    evidence, via the status-callback webhook) compared against the
    UsageEvent Zoiko Local's own metering should have recorded for each
    one. Still two-way against the ZoikoNex ledger itself (that side is
    still a mock with nothing external to compare against), but this makes
    the job genuinely three-record-source, not just Zoiko-Local-vs-itself.
    """

    __tablename__ = "zoikonex_reconciliation_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    total_subscriptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unsynced_subscriptions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_usage_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unsynced_usage_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_completed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmatched_completed_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # New exception rows created by THIS run - re-running while a prior
    # exception is still open never double-counts it here (see
    # app.billing.service.run_zoikonex_reconciliation), so this is "newly
    # found drift," not "total open drift" (that's a live count via
    # list_zoikonex_reconciliation_exceptions(resolved=False)).
    exceptions_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ZoikoNexReconciliationExceptionType(str, enum.Enum):
    SUBSCRIPTION_MISSING_ZOIKONEX_REF = "subscription_missing_zoikonex_ref"
    USAGE_EVENT_MISSING_SYNC = "usage_event_missing_sync"
    # The carrier-evidence leg - see ZoikoNexReconciliationRun's docstring.
    CALL_RECORD_MISSING_USAGE_EVENT = "call_record_missing_usage_event"


class ZoikoNexReconciliationException(Base):
    """Architecture doc §9's "operations queue" for reconciliation
    exceptions - one row per specific out-of-sync record (a Subscription
    with no zoikonex_ref, a UsageEvent with no matching USAGE_SYNC ledger
    row, or a carrier-confirmed completed call with no matching UsageEvent)
    rather than only an aggregate count, so staff can work through them
    individually. resolved_at/resolved_by/resolution_reason follow the
    same "manual override reason" pattern the Architecture doc §10 calls
    for under Business controls, matching this codebase's existing
    number-renewal worklist (mark_number_renewed)."""

    __tablename__ = "zoikonex_reconciliation_exceptions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("zoikonex_reconciliation_runs.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    exception_type: Mapped[ZoikoNexReconciliationExceptionType] = mapped_column(
        Enum(ZoikoNexReconciliationExceptionType, name="zoikonex_reconciliation_exception_type_enum"),
        nullable=False, index=True,
    )
    # The Subscription.id or UsageEvent.id this exception is about,
    # depending on exception_type - not a real FK since it points at
    # different tables depending on type (same "polymorphic reference"
    # tradeoff as AuditEvent.target being a free-form string).
    subject_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    detail: Mapped[str] = mapped_column(String(255), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
