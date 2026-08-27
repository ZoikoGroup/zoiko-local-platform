import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing import service
from app.billing.models import BillingActionRequestStatus, BillingActionType, BillingPeriod
from app.billing.schemas import (
    AIReceptionistAddonRateResponse,
    ApprovePriceCatalogEntryRequest,
    BillingActionRequestResponse,
    CancelSubscriptionRequest,
    ChangePlanRequest,
    CreatePriceCatalogEntryRequest,
    ConfirmPlanChangeRequest,
    CreditNoteResponse,
    CustomerBillingHistoryEntryResponse,
    DebitNoteResponse,
    EntitlementSnapshotResponse,
    PlanChangePreviewResponse,
    PreviewPlanChangeRequest,
    IssueCreditNoteRequest,
    IssueDebitNoteRequest,
    PlanChangeCheckoutSessionResponse,
    PlanResponse,
    PriceCatalogEntryResponse,
    PublicSupportedCountryResponse,
    RefundPaymentRequest,
    RejectBillingActionRequest,
    ResolveReconciliationExceptionRequest,
    RunBillingCycleRequest,
    SetAIReceptionistAddonRequest,
    SimulatePaymentEventRequest,
    SubscriptionResponse,
    TerminateSubscriptionRequest,
    UsageSummaryResponse,
    ZoikoNexReconciliationExceptionResponse,
    ZoikoNexReconciliationRunResponse,
    ZoikoNexReconciliationSummary,
    ZoikoNexSyncEventResponse,
)
from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_capability
from app.core.rate_limit import limiter
from app.integrations.billing import stripe_checkout
from app.integrations.billing import zoikonex as zoikonex_adapter
from app.numbering.identity.models import User
from app.numbering.numbers.models import MarketActivationStatus
from app.numbering.numbers import service as numbers_service
from app.ops.service import KillSwitchTrippedError
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(db: Session = Depends(get_db), _current_user: User = Depends(get_current_user)):
    return service.list_plans(db)


@router.get("/price-catalog/{plan_code}", response_model=PriceCatalogEntryResponse | None)
def get_price_catalog_entry(
    plan_code: str, billing_period: str = "monthly",
    db: Session = Depends(get_db), _current_user: User = Depends(get_current_user),
):
    """Customer-facing (not staff-only, unlike every other /price-catalog
    route below) - any authenticated customer choosing a plan needs to see
    what it costs. Was staff-only until an acceptance-test-style UI sweep
    (2026-08-14) found the dashboard billing page had no price display at
    all - a customer couldn't see what any plan cost, partly because this
    was the only endpoint that could tell them and it 403'd for their own
    account's login. Nothing frontend or staff-side called this route
    before this change, so nothing depends on the old staff-only gate.
    billing_period defaults MONTHLY - annual pricing (added later) is opt-in
    via the query param, not a breaking change to existing callers."""
    return service.get_active_price_catalog_entry(db, plan_code, billing_period=BillingPeriod(billing_period))


@router.get("/public/plans", response_model=list[PlanResponse])
@limiter.limit("60/minute")
def list_public_plans(request: Request, db: Session = Depends(get_db)):
    """Unauthenticated mirror of GET /plans above - powers the public
    marketing pricing page (no visitor account exists yet to authenticate
    with). Same service call, no auth dependency. Rate-limited since this
    is now reachable by anyone on the internet, not just logged-in users."""
    return service.list_plans(db)


@router.get("/public/plans/{plan_code}/price", response_model=PriceCatalogEntryResponse | None)
@limiter.limit("60/minute")
def get_public_plan_price(
    request: Request, plan_code: str, billing_period: str = "monthly", db: Session = Depends(get_db),
):
    """Unauthenticated mirror of GET /price-catalog/{plan_code} above - same
    reasoning as list_public_plans. Commercial values must come from the
    versioned catalog even on the public marketing page, never be
    hardcoded in the frontend."""
    return service.get_active_price_catalog_entry(db, plan_code, billing_period=BillingPeriod(billing_period))


@router.get("/public/countries", response_model=list[PublicSupportedCountryResponse])
@limiter.limit("60/minute")
def list_public_countries(request: Request, db: Session = Depends(get_db)):
    """Unauthenticated, and deliberately filtered to PAID_OPEN only - unlike
    GET /numbers/countries (authenticated, returns every market_status for
    the dashboard's own use), a logged-out visitor should only see markets
    that are actually real and sellable today, not internal-test/closed
    ones still being rolled out."""
    countries = numbers_service.list_supported_countries(db)
    return [c for c in countries if c.market_status == MarketActivationStatus.PAID_OPEN]


@router.get("/ai-receptionist-addon-rate", response_model=AIReceptionistAddonRateResponse | None)
def get_ai_receptionist_addon_rate(db: Session = Depends(get_db), _current_user: User = Depends(get_current_user)):
    """Customer-facing, same posture as GET /price-catalog/{plan_code} above -
    a customer deciding whether to buy the AI Receptionist add-on needs to
    see its price before toggling it, not just after."""
    return service.get_active_ai_receptionist_addon_rate(db)


@router.post("/price-catalog", response_model=PriceCatalogEntryResponse, status_code=status.HTTP_201_CREATED)
def create_price_catalog_entry(
    payload: CreatePriceCatalogEntryRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.manage_price_catalog")),
):
    """SUPER_ADMIN only - Commercial Billing Operating Standard P0-1. Always
    creates a NEW catalog_version row; never edits an existing one (Class A -
    see PriceCatalogEntry's docstring)."""
    try:
        return service.create_price_catalog_entry(
            db, plan_code=payload.plan_code, catalog_version=payload.catalog_version,
            billing_period=BillingPeriod(payload.billing_period),
            amount_minor_units=payload.amount_minor_units, currency_code=payload.currency_code,
            is_placeholder=payload.is_placeholder, actor=staff.id,
            price_book_version=payload.price_book_version, market=payload.market,
            effective_from=payload.effective_from, effective_to=payload.effective_to,
        )
    except service.PriceCatalogEntryExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/price-catalog/{entry_id}/approve", response_model=PriceCatalogEntryResponse)
def approve_price_catalog_entry(
    entry_id: str, payload: ApprovePriceCatalogEntryRequest, db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.manage_price_catalog")),
):
    """SUPER_ADMIN only. Refuses a placeholder entry - see
    CannotApprovePlaceholderError. approval_evidence is the Commercial/
    Finance approval reference, required per the Production Readiness
    Standard's price-book field list."""
    try:
        return service.approve_price_catalog_entry(db, entry_id, actor=staff.id, approval_evidence=payload.approval_evidence)
    except service.PriceCatalogEntryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.CannotApprovePlaceholderError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.post("/price-catalog/{entry_id}/activate", response_model=PriceCatalogEntryResponse)
def activate_price_catalog_entry(
    entry_id: str, db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.manage_price_catalog")),
):
    """SUPER_ADMIN only. Promotes an APPROVED entry to ACTIVE - the step
    that makes it chargeable outside development. Retires whatever was
    previously ACTIVE for the same plan+market."""
    try:
        return service.activate_price_catalog_entry(db, entry_id, actor=staff.id)
    except service.PriceCatalogEntryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.CannotActivateEntryError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.get("/subscription", response_model=SubscriptionResponse)
def get_subscription(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.get_or_create_subscription(db, current_user.account_id)


@router.get("/entitlements", response_model=EntitlementSnapshotResponse)
def get_entitlements(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """ZL-COM-ENT-001 v3.0 - the caller's full resolved entitlement
    snapshot, so the frontend can lock nav items / show plan comparisons
    without guessing at plan tiers client-side."""
    return EntitlementSnapshotResponse(entitlements=service.get_entitlement_snapshot(db, current_user.account_id))


@router.put("/subscription/plan", response_model=SubscriptionResponse)
def change_plan(
    payload: ChangePlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Owner/Admin only, matching every other account-wide commercial
    decision in this app. Kept for staff/internal use (e.g. a comped
    upgrade, or restoring a plan after a support-approved correction) -
    the customer-facing upgrade path is now POST /subscription/plan/
    checkout-session below, which requires real Stripe payment before this
    same change_plan logic ever runs. Calling this route directly still
    changes entitlements immediately with no payment collected, by design,
    for exactly those internal cases - it is deliberately not exposed as
    a button in the customer dashboard."""
    try:
        return service.change_plan(
            db, current_user.account_id, payload.plan_code, actor=current_user.id,
            billing_period=BillingPeriod(payload.billing_period),
        )
    except service.PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/subscription/plan/preview", response_model=PlanChangePreviewResponse)
def preview_plan_change(
    payload: PreviewPlanChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ZL-COM-ENT-001 v3.0 §7/§8 "Preview" stage - a pure read, never
    mutates anything. Returns the signed preview_token POST /subscription/
    plan/confirm needs."""
    try:
        return service.preview_plan_change(
            db, current_user.account_id, payload.plan_code, billing_period=BillingPeriod(payload.billing_period),
        )
    except service.PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/subscription/plan/confirm", response_model=SubscriptionResponse)
def confirm_plan_change(
    payload: ConfirmPlanChangeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ZL-COM-ENT-001 v3.0 §7/§8 "Confirm" stage - an upgrade applies
    immediately; a downgrade is scheduled for the end of the current paid
    period (see service.confirm_plan_change's docstring). PlanChangePreview
    ExpiredError/StaleError aren't caught here - both subclass EntitlementError,
    handled by the global entitlement_error_handler, same as every other
    EntitlementError subclass in this module."""
    try:
        return service.confirm_plan_change(db, current_user.account_id, payload.preview_token, actor=current_user.id)
    except service.PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/subscription/plan/cancel-scheduled", response_model=SubscriptionResponse)
def cancel_scheduled_plan_change(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """ZL-COM-ENT-001 v3.0 §7 "Confirm" stage note - "Scheduled change can
    be canceled before the boundary." Undoes a pending downgrade from
    POST /subscription/plan/confirm; no-ops with a 409 if nothing is
    scheduled."""
    try:
        return service.cancel_scheduled_plan_change(db, current_user.account_id, actor=current_user.id)
    except service.NoScheduledPlanChangeError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/subscription/plan/checkout-session", response_model=PlanChangeCheckoutSessionResponse)
def create_plan_change_checkout_session(
    payload: ChangePlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """The customer-facing way to upgrade a plan goes through here, not
    PUT /subscription/plan directly - Production Readiness Standard doc:
    "A payment-success UI is not the same as an authoritative paid
    invoice." The frontend must redirect the browser to the returned
    `url`; the plan itself only changes once Stripe confirms payment via
    POST /stripe/checkout-webhook below."""
    try:
        return service.create_plan_change_checkout_session(
            db, current_user.account_id, payload.plan_code, actor=current_user.id,
            billing_period=BillingPeriod(payload.billing_period),
        )
    except service.PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.PriceUnavailableForCheckoutError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except stripe_checkout.PaymentError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/stripe/checkout-webhook", status_code=status.HTTP_204_NO_CONTENT)
async def stripe_checkout_webhook(request: Request, db: Session = Depends(get_db)):
    """Real inbound Stripe -> Zoiko Local payment-completion webhook for
    subscription plan upgrades. Unauthenticated by user session (Stripe
    isn't one of our users) - trust comes entirely from the Stripe-
    Signature HMAC, same posture as the ZoikoNex and Stripe Identity
    webhooks elsewhere in this codebase.

    Real gap fix: this used to handle checkout.session.completed only -
    every other event type Stripe actually sends for a real, live,
    mode="subscription" Checkout (which auto-recurs entirely on Stripe's
    own side once it completes, independent of ZoikoNex) was silently
    ignored. invoice.payment_failed/invoice.paid (renewal only - a
    subscription_create invoice.paid is the SAME event checkout.session.
    completed already handles, so treating it as a "restoration" here
    too would be a no-op at best) and customer.subscription.deleted now
    feed the same PAST_DUE/grace-period machinery real ZoikoNex payment
    events already used - see billing.service.
    handle_stripe_subscription_payment_webhook's docstring for the
    customer-facing bug this closes.

    .to_dict() everywhere below (real bug, confirmed live): stripe-python
    15.x's StripeObject no longer subclasses dict, so it has no .get() at
    all - a bare .get() call raises AttributeError (surfaced as a 500,
    since stripe_checkout.construct_webhook_event lets a genuine
    stripe.Event object through, not a plain dict). This affected even
    the pre-existing checkout.session.completed branch's metadata lookup,
    not just the new event types added in this pass - a real Stripe
    webhook delivery for ANY event type on this route would have 500'd
    before this fix, meaning no plan upgrade could ever actually complete
    even with a fully configured, live webhook endpoint. Only
    __getitem__ (event["type"]) and .to_dict() are safe; .get() is not."""
    body = await request.body()
    signature = request.headers.get("Stripe-Signature")

    try:
        event = stripe_checkout.construct_webhook_event(body, signature)
    except stripe_checkout.PaymentError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

    stripe_event_id = event["id"]

    if event["type"] == "checkout.session.completed":
        session_object = event["data"]["object"].to_dict()
        checkout_record_id = session_object.get("metadata", {}).get("checkout_record_id")
        if not checkout_record_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing checkout_record_id metadata")
        try:
            service.handle_stripe_checkout_completed(
                db, checkout_record_id=checkout_record_id,
                stripe_subscription_id=session_object.get("subscription"),
            )
        except service.PlanChangeCheckoutSessionNotFoundError as e:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"].to_dict()
        stripe_subscription_id = invoice.get("subscription")
        if stripe_subscription_id:
            service.handle_stripe_subscription_payment_webhook(
                db, stripe_subscription_id=stripe_subscription_id, event_type="payment_failed",
                stripe_event_id=stripe_event_id,
            )
    elif event["type"] == "invoice.paid":
        invoice = event["data"]["object"].to_dict()
        stripe_subscription_id = invoice.get("subscription")
        # subscription_create is the SAME successful payment checkout.
        # session.completed above already applies - only a genuine
        # renewal (subscription_cycle) or a manual retry after a prior
        # failure (subscription_update) means "restore from PAST_DUE."
        if stripe_subscription_id and invoice.get("billing_reason") != "subscription_create":
            service.handle_stripe_subscription_payment_webhook(
                db, stripe_subscription_id=stripe_subscription_id, event_type="payment_restored",
                stripe_event_id=stripe_event_id,
            )
    elif event["type"] == "customer.subscription.deleted":
        stripe_subscription_object = event["data"]["object"].to_dict()
        stripe_subscription_id = stripe_subscription_object.get("id")
        if stripe_subscription_id:
            service.handle_stripe_subscription_deleted_webhook(db, stripe_subscription_id=stripe_subscription_id)
    return None


@router.post("/subscription/cancel", response_model=SubscriptionResponse)
def cancel_subscription(
    payload: CancelSubscriptionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Owner/Admin only, matching change_plan above - the account's own
    decision about its own subscription, not one of the 4 staff maker-
    checker money-moving actions elsewhere in this file. Immediate, not
    "at period end" - see service.cancel_subscription's docstring."""
    try:
        return service.cancel_subscription(db, current_user.account_id, actor=current_user.id, reason=payload.reason)
    except service.SubscriptionAlreadyCanceledError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except stripe_checkout.PaymentError as e:
        # Cancellation deliberately did not apply locally if this failed -
        # see service.cancel_subscription's docstring - so this is a real
        # "nothing changed, please retry" error, not a partial success.
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.put("/subscription/ai-receptionist-addon", response_model=SubscriptionResponse)
def set_ai_receptionist_addon(
    payload: SetAIReceptionistAddonRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Owner/Admin only, same commercial-decision gating as change_plan
    above. Pricing doc §5.3 $29/workspace/month AI Receptionist add-on -
    purely local entitlement flip; no real payment is collected here any
    more than change_plan collects one (see run_billing_cycle for why -
    this doesn't add an invoice line yet)."""
    return service.set_ai_receptionist_addon(db, current_user.account_id, enabled=payload.enabled, actor=current_user.id)


@router.get("/usage-summary", response_model=UsageSummaryResponse)
def usage_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.get_usage_summary(db, current_user.account_id)


@router.get("/invoices", response_model=list[CustomerBillingHistoryEntryResponse])
def list_own_invoices(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Customer-facing billing history - invoices, captured payments,
    credit/debit notes, refunds for THIS account only (never an arbitrary
    account_id like the staff-only /zoikonex/sync-log below). Returns a
    curated field allow-list (CustomerBillingHistoryEntryResponse), never
    the raw sync-log payload - see that schema's docstring. Any
    authenticated member can view - same bar as calling-rates, not
    Owner/Admin-only like the raw usage ledger."""
    return service.list_account_billing_history(db, current_user.account_id)


@router.post("/zoikonex/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def zoikonex_payment_webhook(request: Request, db: Session = Depends(get_db)):
    """Real inbound ZoikoNex -> Zoiko Local payment-state webhook (Architecture
    doc §9). Unauthenticated by user session (ZoikoNex isn't one of our users)
    - trust comes entirely from the HMAC signature, same posture as the
    Stripe Identity webhook at compliance/routes.py. Expected payload shape
    ({event_id, event_type, zoikonex_ref}) and the X-ZoikoNex-Signature
    scheme are both placeholders pending ZoikoNex's actual locked contract -
    see app.integrations.billing.zoikonex's docstring."""
    body = await request.body()
    signature = request.headers.get("X-ZoikoNex-Signature")

    try:
        zoikonex_adapter.verify_webhook_signature(body, signature)
    except zoikonex_adapter.ZoikoNexWebhookError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

    try:
        payload = json.loads(body)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed JSON body") from e

    zoikonex_ref = payload.get("zoikonex_ref")
    event_type = payload.get("event_type")
    if not zoikonex_ref or not event_type:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="zoikonex_ref and event_type are required")

    try:
        service.handle_zoikonex_payment_webhook(
            db, zoikonex_ref=zoikonex_ref, event_type=event_type, external_event_id=payload.get("event_id"),
        )
    except service.ZoikoNexRefNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.InvalidPaymentEventError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return None


# --- Staff-only: mock ZoikoNex operations console ---
# There's no real ZoikoNex to send these webhooks for real yet - these
# routes exist so the graceful-degradation mechanism (Architecture doc §9)
# can actually be exercised and demonstrated end-to-end before a real
# connection exists. See app.integrations.billing.zoikonex's docstring.


@router.post("/zoikonex/simulate-payment-event", response_model=SubscriptionResponse)
def simulate_payment_event(
    payload: SimulatePaymentEventRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.simulate_payment_event")),
):
    """SUPER_ADMIN only (via the staff_capability_grants matrix) - this
    simulates a real billing-state change (Architecture doc §11
    "segregation of duties for sensitive actions"), the same bar as KYC
    approve/reject."""
    try:
        return service.simulate_zoikonex_payment_event(db, payload.account_id, payload.event_type, actor=staff.id)
    except service.InvalidPaymentEventError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.get("/zoikonex/sync-log", response_model=list[ZoikoNexSyncEventResponse])
def zoikonex_sync_log(
    account_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_zoikonex_sync_events(db, account_id=account_id, limit=limit)


@router.get("/zoikonex/reconciliation", response_model=ZoikoNexReconciliationSummary)
def zoikonex_reconciliation(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.get_zoikonex_reconciliation_summary(db)


@router.post("/zoikonex/reconciliation/run", response_model=ZoikoNexReconciliationRunResponse)
def run_zoikonex_reconciliation(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Architecture doc §9 daily reconciliation job. Any staff role can
    trigger this - same diagnostic (not approval-action) posture as
    /ops/synthetic-checks/run; the exceptions it finds are read-only
    findings until a SUPER_ADMIN resolves one below."""
    return service.run_zoikonex_reconciliation(db)


@router.get("/zoikonex/reconciliation/runs", response_model=list[ZoikoNexReconciliationRunResponse])
def list_zoikonex_reconciliation_runs(
    limit: int = 200,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_zoikonex_reconciliation_runs(db, limit=limit)


@router.get("/zoikonex/reconciliation/exceptions", response_model=list[ZoikoNexReconciliationExceptionResponse])
def list_zoikonex_reconciliation_exceptions(
    resolved: bool | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_zoikonex_reconciliation_exceptions(db, resolved=resolved, limit=limit)


@router.post("/zoikonex/reconciliation/wholesale-cost-capture/run")
def run_wholesale_cost_capture(
    limit: int = 50,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """P0-8 "retail vs wholesale reconciliation" - fetches Twilio's own real
    Call resource price for completed calls missing a wholesale cost.
    Staff-triggered, same diagnostic (not approval-action) posture as
    /zoikonex/reconciliation/run above - there's no scheduler in this
    codebase to run it automatically yet."""
    return service.capture_wholesale_call_cost(db, limit=limit)


@router.get("/zoikonex/reconciliation/wholesale-summary")
def zoikonex_wholesale_reconciliation_summary(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Retail-vs-wholesale margin summary - read-only, so any staff role
    can view it, same bar as /zoikonex/reconciliation above."""
    return service.get_wholesale_reconciliation_summary(db)


@router.post(
    "/zoikonex/reconciliation/exceptions/{exception_id}/resolve",
    response_model=ZoikoNexReconciliationExceptionResponse,
)
def resolve_zoikonex_reconciliation_exception(
    exception_id: str,
    payload: ResolveReconciliationExceptionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.resolve_reconciliation_exception")),
):
    """SUPER_ADMIN only (via the staff_capability_grants matrix) -
    resolving a billing-reconciliation exception is a
    manual override of money-adjacent state (Architecture doc §10
    "segregation of duties for sensitive actions"), the same bar as
    simulate_zoikonex_payment_event above."""
    try:
        return service.resolve_zoikonex_reconciliation_exception(
            db, exception_id, actor=staff.id, reason=payload.reason
        )
    except service.ReconciliationExceptionNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/zoikonex/run-billing-cycle/request", response_model=BillingActionRequestResponse, status_code=201)
def request_run_billing_cycle(
    payload: RunBillingCycleRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.run_billing_cycle")),
):
    """SUPER_ADMIN only - stages a real rating -> invoice -> payment cycle
    against ZoikoNex for one account, priced from PriceCatalogEntry (see
    that model's docstring - may still be a placeholder, not a real
    decided price), for a *different* staff member to approve via POST
    /billing/actions/{id}/approve - see BillingActionRequest's docstring
    for why this is no longer a single-staff action."""
    return service.request_billing_action(
        db, action_type=BillingActionType.RUN_BILLING_CYCLE,
        payload=payload.model_dump(), requested_by=staff.id,
    )


@router.post("/zoikonex/credit-notes/request", response_model=BillingActionRequestResponse, status_code=201)
def request_issue_credit_note(
    payload: IssueCreditNoteRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.issue_credit_note")),
):
    """SUPER_ADMIN only - stages a correction to an over-billed ISSUED
    invoice for a different staff member to approve."""
    return service.request_billing_action(
        db, action_type=BillingActionType.CREDIT_NOTE,
        payload=payload.model_dump(), requested_by=staff.id,
    )


@router.post("/zoikonex/debit-notes/request", response_model=BillingActionRequestResponse, status_code=201)
def request_issue_debit_note(
    payload: IssueDebitNoteRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.issue_debit_note")),
):
    """SUPER_ADMIN only - stages a correction to an under-billed ISSUED
    invoice for a different staff member to approve."""
    return service.request_billing_action(
        db, action_type=BillingActionType.DEBIT_NOTE,
        payload=payload.model_dump(), requested_by=staff.id,
    )


@router.post("/zoikonex/refunds/request", response_model=BillingActionRequestResponse, status_code=201)
def request_refund_payment(
    payload: RefundPaymentRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.refund_payment")),
):
    """SUPER_ADMIN only - stages a refund of a CAPTURED ZoikoNex payment
    for a different staff member to approve. Currently always fails on
    approval against a real ZoikoNex-side error (capture itself is broken
    there - see app.integrations.billing.zoikonex's docstring), not a bug
    here - see refund_zoikonex_payment's docstring."""
    return service.request_billing_action(
        db, action_type=BillingActionType.REFUND,
        payload=payload.model_dump(), requested_by=staff.id,
    )


@router.post("/subscription/terminate/request", response_model=BillingActionRequestResponse, status_code=201)
def request_terminate_subscription(
    payload: TerminateSubscriptionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.terminate_subscription")),
):
    """SUPER_ADMIN only - stages the terminal, one-way deprovisioning of a
    canceled/past-due subscription (Commercial Billing Operating Standard
    doc §M3) for a different staff member to approve - same segregation-of-
    duties bar as every other sensitive billing action here."""
    return service.request_billing_action(
        db, action_type=BillingActionType.TERMINATE_SUBSCRIPTION,
        payload=payload.model_dump(), requested_by=staff.id,
    )


@router.get("/actions", response_model=list[BillingActionRequestResponse])
def list_billing_actions(
    request_status: str | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Any staff role can view the queue (diagnostic, same posture as other
    review-queue list endpoints in this codebase); approving/rejecting one
    is the sensitive action, gated below."""
    status_filter = BillingActionRequestStatus(request_status) if request_status else None
    return service.list_billing_action_requests(db, status=status_filter)


@router.post("/actions/{action_id}/approve", response_model=BillingActionRequestResponse)
def approve_billing_action(
    action_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.approve_billing_action")),
):
    """SUPER_ADMIN only, AND must be a different staff member than
    whoever requested the action (see approve_billing_action's docstring
    in the service layer) - the actual maker-checker enforcement. Executes
    the real ZoikoNex call using the exact payload that was staged."""
    try:
        return service.approve_billing_action(db, action_id, actor=staff.id)
    except service.BillingActionRequestNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.BillingActionAlreadyResolvedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.SelfApprovalNotAllowedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except service.NonCommercialAccountError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except service.ZoikoNexBillingCycleError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except zoikonex_adapter.ZoikoNexError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except service.SubscriptionAlreadyTerminatedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.SubscriptionNotEligibleForTerminationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except service.TestAccountRestrictedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.post("/actions/{action_id}/reject", response_model=BillingActionRequestResponse)
def reject_billing_action(
    action_id: str,
    payload: RejectBillingActionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.approve_billing_action")),
):
    """Same dual-control bar as approve - a different staff member must
    review it, even to reject."""
    try:
        return service.reject_billing_action(db, action_id, actor=staff.id, reason=payload.reason)
    except service.BillingActionRequestNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.BillingActionAlreadyResolvedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.SelfApprovalNotAllowedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
