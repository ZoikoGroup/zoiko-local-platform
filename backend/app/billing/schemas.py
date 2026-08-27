from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_code: str
    name: str
    max_numbers: int
    max_team_seats: int
    monthly_voice_minutes: int
    monthly_video_minutes: int
    monthly_ai_summaries: int
    included_ai_receptionist_minutes: int
    trial_days: int


class PublicSupportedCountryResponse(BaseModel):
    """Deliberately thinner than SupportedCountryResponse (numbering.numbers.
    schemas) - this backs the public, unauthenticated /billing/public/countries
    endpoint, so market_status/legal_signoff fields (internal rollout state)
    aren't leaked to the open internet; the route itself already filters to
    PAID_OPEN only."""
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


class AIReceptionistAddonRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    catalog_version: str
    monthly_price_minor_units: int
    included_minutes: int
    overage_rate_minor_units_per_minute: int
    currency_code: str
    is_placeholder: bool


class PriceCatalogEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_code: str
    catalog_version: str
    billing_period: str
    amount_minor_units: int
    currency_code: str
    status: str
    is_placeholder: bool
    price_book_version: str | None
    market: str
    effective_from: datetime | None
    effective_to: datetime | None
    approval_evidence: str | None
    approved_by: str | None
    approved_at: datetime | None
    created_at: datetime


class CreatePriceCatalogEntryRequest(BaseModel):
    plan_code: str
    catalog_version: str
    billing_period: str = "monthly"
    amount_minor_units: int
    currency_code: str = "USD"
    is_placeholder: bool = True
    price_book_version: str | None = None
    market: str = "GLOBAL"
    effective_from: datetime | None = None
    effective_to: datetime | None = None


class ApprovePriceCatalogEntryRequest(BaseModel):
    approval_evidence: str


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_code: str
    status: str
    billing_period: str
    ai_receptionist_addon_enabled: bool
    trial_ends_at: datetime | None
    current_period_start: datetime
    current_period_end: datetime
    zoikonex_ref: str | None
    grace_period_ends_at: datetime | None
    canceled_at: datetime | None


class ChangePlanRequest(BaseModel):
    plan_code: str
    billing_period: str = "monthly"


class PlanChangeCheckoutSessionResponse(BaseModel):
    """Real Stripe-hosted Checkout Session for a plan upgrade - unlike
    numbering.numbers.schemas.CheckoutSessionResponse (id/url always None,
    kept only for old-client compatibility), both fields here are always
    populated: the frontend must redirect the browser to `url`."""

    id: str
    url: str


class CancelSubscriptionRequest(BaseModel):
    reason: str | None = None


class SetAIReceptionistAddonRequest(BaseModel):
    enabled: bool


class UsageResourceSummary(BaseModel):
    resource: str
    used: float
    limit: int
    # Pricing doc §5.3 included-allowance + overage - only populated for
    # ai_receptionist_minutes today; None for every other resource, which
    # has no overage-billing concept at all.
    overage_minutes: float | None = None
    estimated_overage_cost_cents: int | None = None


class UsageSummaryResponse(BaseModel):
    plan_code: str
    plan_name: str
    status: str
    trial_ends_at: datetime | None
    current_period_start: datetime
    current_period_end: datetime
    ai_receptionist_addon_enabled: bool
    is_suspended: bool
    ai_receptionist_enabled: bool
    resources: list[UsageResourceSummary]


class SimulatePaymentEventRequest(BaseModel):
    account_id: str
    event_type: str  # "payment_failed" | "payment_retry" | "payment_restored"


class RunBillingCycleRequest(BaseModel):
    account_id: str


class TerminateSubscriptionRequest(BaseModel):
    account_id: str
    reason: str | None = None


class RunBillingCycleResponse(BaseModel):
    billed: bool
    reason: str | None = None
    plan_code: str | None = None
    amount_minor_units: int | None = None
    invoice_id: str | None = None
    payment_intent_id: str | None = None
    invoice_status: str | None = None
    payment_status: str | None = None
    captured: bool | None = None
    capture_error: str | None = None
    bill_cycle_closed: bool | None = None
    bill_cycle_close_error: str | None = None


class IssueCreditNoteRequest(BaseModel):
    account_id: str
    invoice_id: str
    amount_minor_units: int
    reason: str


class IssueDebitNoteRequest(BaseModel):
    account_id: str
    invoice_id: str
    amount_minor_units: int
    reason: str


class RefundPaymentRequest(BaseModel):
    account_id: str
    payment_intent_id: str
    amount_minor_units: int
    reason: str


class CreditNoteResponse(BaseModel):
    credit_note_id: str
    status: str | None = None
    amount_minor_units: int | None = None


class DebitNoteResponse(BaseModel):
    debit_note_id: str
    status: str | None = None
    amount_minor_units: int | None = None


class RefundResponse(BaseModel):
    refund_id: str | None = None
    status: str | None = None


class ZoikoNexSyncEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    event_type: str
    zoikonex_ref: str | None
    payload: dict
    created_at: datetime


class CustomerBillingHistoryEntryResponse(BaseModel):
    """GET /billing/invoices - deliberately NOT ZoikoNexSyncEventResponse.
    That schema's `payload: dict` is an unfiltered internal diagnostic blob
    (bill_cycle_close_error/capture_error carry raw ZoikoNex-side error
    text - see run_billing_cycle's docstring on the confirmed-live bugs
    those come from; placeholder_price is business-sensitive pricing-
    governance metadata) - fine for GET /billing/zoikonex/sync-log
    (staff-only), never fine for a paying customer. This is an explicit
    allow-list of fields, not a filtered copy of that payload."""

    id: str
    event_type: str
    reference: str | None
    amount_minor_units: int | None
    status: str | None
    reason: str | None
    created_at: datetime


class ZoikoNexReconciliationSummary(BaseModel):
    total_subscriptions: int
    synced_subscriptions: int
    unsynced_subscriptions: int
    total_usage_events: int
    synced_usage_events: int
    unsynced_usage_events: int


class ZoikoNexReconciliationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    total_subscriptions: int
    unsynced_subscriptions: int
    total_usage_events: int
    unsynced_usage_events: int
    total_completed_calls: int
    unmatched_completed_calls: int
    exceptions_found: int
    created_at: datetime


class ZoikoNexReconciliationExceptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    account_id: str
    exception_type: str
    subject_id: str
    detail: str
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_reason: str | None
    created_at: datetime


class ResolveReconciliationExceptionRequest(BaseModel):
    reason: str


class BillingActionRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action_type: str
    payload: dict
    requested_by: str
    status: str
    approved_by: str | None
    rejection_reason: str | None
    result: dict | None
    resolved_at: datetime | None
    created_at: datetime


class RejectBillingActionRequest(BaseModel):
    reason: str | None = None
