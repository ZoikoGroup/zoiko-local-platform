import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing import service
from app.billing.models import BillingActionRequestStatus, BillingActionType
from app.billing.schemas import (
    BillingActionRequestResponse,
    ChangePlanRequest,
    CreatePriceCatalogEntryRequest,
    IssueCreditNoteRequest,
    IssueDebitNoteRequest,
    PlanResponse,
    PriceCatalogEntryResponse,
    RefundPaymentRequest,
    RejectBillingActionRequest,
    ResolveReconciliationExceptionRequest,
    RunBillingCycleRequest,
    SimulatePaymentEventRequest,
    SubscriptionResponse,
    UsageSummaryResponse,
    ZoikoNexReconciliationExceptionResponse,
    ZoikoNexReconciliationRunResponse,
    ZoikoNexReconciliationSummary,
    ZoikoNexSyncEventResponse,
)
from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_capability
from app.integrations.billing import zoikonex as zoikonex_adapter
from app.numbering.identity.models import User
from app.ops.service import KillSwitchTrippedError
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(db: Session = Depends(get_db), _current_user: User = Depends(get_current_user)):
    return service.list_plans(db)


@router.get("/price-catalog/{plan_code}", response_model=PriceCatalogEntryResponse | None)
def get_price_catalog_entry(
    plan_code: str, db: Session = Depends(get_db), _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.get_active_price_catalog_entry(db, plan_code)


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
            amount_minor_units=payload.amount_minor_units, currency_code=payload.currency_code,
            is_placeholder=payload.is_placeholder, actor=staff.id,
        )
    except service.PriceCatalogEntryExistsError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/price-catalog/{entry_id}/approve", response_model=PriceCatalogEntryResponse)
def approve_price_catalog_entry(
    entry_id: str, db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.manage_price_catalog")),
):
    """SUPER_ADMIN only. Refuses a placeholder entry - see
    CannotApprovePlaceholderError."""
    try:
        return service.approve_price_catalog_entry(db, entry_id, actor=staff.id)
    except service.PriceCatalogEntryNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.CannotApprovePlaceholderError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.get("/subscription", response_model=SubscriptionResponse)
def get_subscription(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.get_or_create_subscription(db, current_user.account_id)


@router.put("/subscription/plan", response_model=SubscriptionResponse)
def change_plan(
    payload: ChangePlanRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Owner/Admin only, matching every other account-wide commercial
    decision in this app. Purely local plan reassignment - no real payment
    is collected or processed anywhere in this system; it changes which
    entitlement limits apply and syncs the change to the MOCK ZoikoNex
    adapter (see app.integrations.billing.zoikonex's docstring)."""
    try:
        return service.change_plan(db, current_user.account_id, payload.plan_code, actor=current_user.id)
    except service.PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/usage-summary", response_model=UsageSummaryResponse)
def usage_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.get_usage_summary(db, current_user.account_id)


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
