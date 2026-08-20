import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    # Commercial Billing Operating Standard doc §M3 - a distinct, final state
    # from CANCELED: CANCELED still lets an Owner/Admin resubscribe via
    # change_plan (see assert_billing_not_suspended's docstring), so on its
    # own it behaves like an indefinite suspension, not a real termination.
    # TERMINATED additionally deprovisions every owned number and is a
    # one-way door - see terminate_subscription.
    TERMINATED = "terminated"


class Plan(Base):
    """Architecture doc §5: "Subscription and Entitlement... Plans, limits,
    feature gates, number allowances, minute/video/AI quotas, grace
    periods" - a Zoiko-owned service, explicitly separate from the
    "ZoikoNex Billing Adapter" row in the same table. Stored as data (not
    hardcoded limits in Python), same discipline as this project's
    compliance rules, so plan limits can be tuned without a deploy.

    No price fields - the price authority is PriceCatalogEntry below (and,
    once registered, ZoikoNex's own product-catalogue-commercial service).
    This table purely gates feature/resource usage.
    """

    __tablename__ = "plans"

    plan_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    max_numbers: Mapped[int] = mapped_column(Integer, nullable=False)
    max_team_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_voice_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_video_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    # Per-room capacity, not a monthly quota (see monthly_video_minutes for
    # that) - Roadmap doc §8: "Phase 1 capacity: 1:1 and small-group video,
    # target up to 8 participants" vs the later "larger meetings" tier
    # (Architecture doc Phase 3). Passed straight to the Provider Gateway's
    # create_room() (see media.service.create_video_session) - previously
    # every plan silently got the same flat 50-participant LiveKit-side cap
    # regardless of tier.
    max_video_participants: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8")
    monthly_ai_summaries: Mapped[int] = mapped_column(Integer, nullable=False)
    # Pricing doc §4 plan comparison table: Pro includes 50 min/account/month
    # of AI Receptionist usage, Scale includes 150 - "account/workspace-level,
    # not multiplied by seat count" per the doc's own footnote, which is
    # already this column's granularity (one value per plan, not per seat).
    # Zero for every other tier - Starter/Business only get AI Receptionist
    # minutes by buying the separate $29/mo add-on (see
    # AIReceptionistAddonRate, Subscription.ai_receptionist_addon_enabled).
    ai_receptionist_minutes_included: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
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


class CatalogEntryStatus(str, enum.Enum):
    # Renamed from the original DRAFT (migration 8f3c1a9d2b47 - ALTER TYPE
    # ... RENAME VALUE, not a new state) to match the Production Readiness
    # & Go-Live Decision Standard's exact vocabulary (§2.3/Table 8):
    # "PROPOSED / APPROVED / ACTIVE / RETIRED".
    PROPOSED = "proposed"
    APPROVED = "approved"
    # New: a plan+market can have many APPROVED historical versions but at
    # most one ACTIVE one at a time (see app.billing.service.
    # activate_price_catalog_entry) - this is the version run_billing_cycle
    # actually charges against, distinct from merely being signed off.
    ACTIVE = "active"
    # New: set automatically on whatever was ACTIVE when a new version is
    # activated for the same plan_code+market. Never deleted - preserves
    # the "past invoices remain reproducible against the version that was
    # active when issued" guarantee even after supersession.
    RETIRED = "retired"


class PriceCatalogEntry(Base):
    """Commercial Billing Operating Standard P0-1 ("Lock the Zoiko Local
    public and contract price catalog... Engineering and Marketing must
    consume a versioned APPROVED price catalog; no plan name, included
    allowance, rate, trial, setup fee or overage may be invented in code
    or copy"). Replaces the old bare TEST_PLACEHOLDER_PRICES Python dict
    (app.integrations.billing.zoikonex) with a real, versioned, queryable
    table - but the VALUES themselves are still the same placeholder
    numbers already explicitly approved for dev/test use, not a real
    commercial decision. is_placeholder=True on every row seeded so far;
    nothing in this codebase treats a placeholder entry as chargeable in
    a non-development environment (see app.billing.service.
    run_billing_cycle's catalog-status gate).

    Class A once APPROVED, same discipline as ZoikoNex's own PriceRule
    (immutable_after_status) - a real price change is a NEW catalog_version
    row, never an edit to an existing one, so past invoices always resolve
    against the exact version that was active when they were issued.

    Production Readiness & Go-Live Decision Standard §2.3/Table 8 fields
    added on top of the original P0-1 shape: price_book_version groups
    entries created/ratified together into one named release (e.g.
    "2026-UK-001") - deliberately separate from catalog_version, which
    stays the existing per-plan Class A version key; market scopes a price
    to a country/commercial market (defaults GLOBAL, since nothing here is
    market-specific yet); effective_from/effective_to bound when a version
    may be sold while preserving historical truth; approval_evidence is
    the Commercial/Finance approval reference (ticket/decision ID),
    distinct from approved_by/approved_at (who and when actually clicked
    approve in this system).
    """

    __tablename__ = "price_catalog_entries"
    __table_args__ = (
        UniqueConstraint("plan_code", "catalog_version", name="uq_price_catalog_entry_plan_version"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    plan_code: Mapped[str] = mapped_column(String(50), ForeignKey("plans.plan_code"), nullable=False, index=True)
    catalog_version: Mapped[str] = mapped_column(String(50), nullable=False)
    amount_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[CatalogEntryStatus] = mapped_column(
        Enum(CatalogEntryStatus, name="catalog_entry_status_enum"), nullable=False, default=CatalogEntryStatus.PROPOSED
    )
    # Explicit, unambiguous marker - deliberately separate from `status`
    # (a PROPOSED row and a placeholder row are conceptually different: a
    # real not-yet-approved price is still a real candidate price, whereas
    # this is fake test data that must never be mistaken for one regardless
    # of what status it's later moved to).
    is_placeholder: Mapped[bool] = mapped_column(nullable=False, default=True)
    price_book_version: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    market: Mapped[str] = mapped_column(String(10), nullable=False, default="GLOBAL", server_default="GLOBAL")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AIReceptionistAddonRate(Base):
    """Pricing doc §5.3's `AIUsageRate` object (ai_product, included
    quantity, overage unit rate, workspace/account scope, effective
    period) for the one AI product that's priced today: the AI
    Receptionist workspace add-on. Deliberately its own table rather than
    another PriceCatalogEntry row - that table's plan_code is a real FK to
    `plans`, and this add-on isn't a subscription tier, it's an
    account-level toggle any plan can turn on (Subscription.
    ai_receptionist_addon_enabled) independent of plan_code. Same Class-A
    versioning discipline as PriceCatalogEntry: a real price change is a
    new catalog_version row, never an edit to an existing one.

    Pro/Scale get their own included-minutes allowance
    (Plan.ai_receptionist_minutes_included) without needing this add-on at
    all - this table's included_minutes only applies once the add-on
    itself is purchased."""

    __tablename__ = "ai_receptionist_addon_rates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    catalog_version: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    monthly_price_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    included_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    overage_rate_minor_units_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    status: Mapped[CatalogEntryStatus] = mapped_column(
        Enum(CatalogEntryStatus, name="catalog_entry_status_enum"), nullable=False, default=CatalogEntryStatus.PROPOSED
    )
    is_placeholder: Mapped[bool] = mapped_column(nullable=False, default=True)
    price_book_version: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approval_evidence: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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
    # Set once, the first time cancel_subscription runs (see that
    # function's docstring) - NULL for every status except CANCELED.
    # Distinct from grace_period_ends_at above: that's an involuntary
    # PAST_DUE clock; this is a voluntary, immediate cancellation with no
    # grace period of its own.
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once, the first time terminate_subscription runs - NULL for every
    # status except TERMINATED. See SubscriptionStatus.TERMINATED's docstring
    # for how this differs from canceled_at above.
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Pricing doc §5.3 "$29.00 per workspace/month" AI Receptionist add-on -
    # an account-level toggle independent of plan_code (see
    # AIReceptionistAddonRate's docstring). Not yet wired into
    # run_billing_cycle's actual charge computation (metering only via
    # get_usage_summary today) - see CLAUDE.md's note on why that's a
    # separate, larger piece of work.
    ai_receptionist_addon_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZoikoNexSyncEventType(str, enum.Enum):
    SUBSCRIPTION_SYNC = "subscription_sync"
    USAGE_SYNC = "usage_sync"
    PAYMENT_EVENT_RECEIVED = "payment_event_received"
    # Real invoice/payment collection via ZoikoNex's billing-invoice/
    # payments services - see app.billing.service.run_billing_cycle.
    INVOICE_GENERATED = "invoice_generated"
    PAYMENT_COLLECTED = "payment_collected"
    # Post-issue corrections (ZN-ADR-012: an ISSUED invoice's monetary
    # fields are immutable - these are the only legal way to fix one) and
    # refunds of a captured payment - see app.billing.service's
    # issue_invoice_credit_note/issue_invoice_debit_note/refund_zoikonex_payment.
    CREDIT_NOTE_ISSUED = "credit_note_issued"
    DEBIT_NOTE_ISSUED = "debit_note_issued"
    REFUND_ISSUED = "refund_issued"


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
    value. Runs daily via app.ops.scheduled_reconciliation (see its
    module docstring - a Render Cron Job service, not in-process); staff
    can also still trigger POST /billing/zoikonex/reconciliation/run
    on demand between scheduled runs.

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
    # P0-8 "late-event policy" - matched (unlike unmatched_completed_calls
    # above) but recorded past LATE_EVENT_THRESHOLD after the call
    # completed.
    late_usage_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # New exception rows created by THIS run - re-running while a prior
    # exception is still open never double-counts it here (see
    # app.billing.service.run_zoikonex_reconciliation), so this is "newly
    # found drift," not "total open drift" (that's a live count via
    # list_zoikonex_reconciliation_exceptions(resolved=False)).
    exceptions_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Production Readiness Standard's "no public production while
    # reconciliation or recovery is uncertain" - a run_billing_cycle
    # capture that failed with ZoikoNexCaptureFailedError (the confirmed
    # ZoikoNex-side gRPC bug - see that error's docstring) used to just sit
    # in a ZoikoNexSyncEvent payload with no queue entry at all. This
    # counts how many of THIS run's new exceptions are that specific type.
    uncaptured_payments_found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ZoikoNexReconciliationExceptionType(str, enum.Enum):
    SUBSCRIPTION_MISSING_ZOIKONEX_REF = "subscription_missing_zoikonex_ref"
    USAGE_EVENT_MISSING_SYNC = "usage_event_missing_sync"
    # The carrier-evidence leg - see ZoikoNexReconciliationRun's docstring.
    CALL_RECORD_MISSING_USAGE_EVENT = "call_record_missing_usage_event"
    # Commercial Billing Operating Standard P0-8 "late-event policy" - the
    # usage event DOES exist (unlike the type above), but wasn't recorded
    # until well after the underlying call completed. Flagged, not
    # silently accepted, because a late event landing after its billing
    # period has already closed and been invoiced needs a human decision
    # (credit the current period vs. hold for the next one), not a guess.
    LATE_USAGE_EVENT = "late_usage_event"
    # A run_billing_cycle capture that failed with ZoikoNexCaptureFailedError
    # (confirmed ZoikoNex-side gRPC bug, not something this repo can fix) -
    # "authorised but not captured" is a real, reportable outcome, not a
    # silent one; this is what actually puts it in the operations queue
    # instead of leaving it discoverable only by querying ZoikoNexSyncEvent
    # payloads by hand.
    PAYMENT_AUTHORISED_NOT_CAPTURED = "payment_authorised_not_captured"


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


class BillingActionType(str, enum.Enum):
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    REFUND = "refund"
    RUN_BILLING_CYCLE = "run_billing_cycle"
    TERMINATE_SUBSCRIPTION = "terminate_subscription"


class BillingActionRequestStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"


class BillingActionRequest(Base):
    """Commercial Billing Operating Standard doc's RBAC/segregation-of-
    duties doctrine (§26: "Approver... cannot self-approve where policy
    applies") for the highest-risk money-moving ZoikoNex actions this
    codebase has (credit note, debit note, refund, run-billing-cycle) -
    every one of them previously executed on a single staff member's
    say-so the moment they held the per-action capability. This table adds
    a second, distinct staff actor: `requested_by` stages the action and
    its original payload; `approved_by` (enforced != requested_by at the
    service layer, see approve_billing_action) actually executes it. Not
    retrofitted onto every sensitive action in this codebase - scoped to
    the 4 newest, real-money-moving ones; extending the same pattern to
    e.g. number release or port cancellation is a separate decision."""

    __tablename__ = "billing_action_requests"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    action_type: Mapped[BillingActionType] = mapped_column(
        Enum(BillingActionType, name="billing_action_type_enum"), nullable=False, index=True,
    )
    # The original request body (account_id, invoice_id/payment_intent_id,
    # amount_minor_units, reason, etc. depending on action_type) - staged
    # here rather than executed immediately, then replayed verbatim by
    # approve_billing_action so the approver is authorizing exactly what
    # was requested, not a re-typed summary of it.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[BillingActionRequestStatus] = mapped_column(
        Enum(BillingActionRequestStatus, name="billing_action_request_status_enum"),
        nullable=False, default=BillingActionRequestStatus.PENDING, index=True,
    )
    approved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # The real ZoikoNex response (credit_note_id/debit_note_id/refund_id/
    # billing-cycle summary) once EXECUTED - an audit trail of what the
    # approval actually did, not just that it happened.
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
