import json

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing import service
from app.billing.schemas import (
    ChangePlanRequest,
    CreditNoteResponse,
    DebitNoteResponse,
    IssueCreditNoteRequest,
    IssueDebitNoteRequest,
    PlanResponse,
    RefundPaymentRequest,
    RefundResponse,
    ResolveReconciliationExceptionRequest,
    RunBillingCycleRequest,
    RunBillingCycleResponse,
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
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/plans", response_model=list[PlanResponse])
def list_plans(db: Session = Depends(get_db), _current_user: User = Depends(get_current_user)):
    return service.list_plans(db)


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


@router.post("/zoikonex/run-billing-cycle", response_model=RunBillingCycleResponse)
def run_billing_cycle(
    payload: RunBillingCycleRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.run_billing_cycle")),
):
    """SUPER_ADMIN only - drives a real rating -> invoice -> payment cycle
    against ZoikoNex for one account, using TEST_PLACEHOLDER_PRICES (see
    app.integrations.billing.zoikonex's docstring - NOT a real decided
    price). Same segregation-of-duties bar as simulate_payment_event above,
    since this creates real ZoikoNex invoices and payment intents, even
    though the amount is a placeholder."""
    try:
        return service.run_billing_cycle(db, payload.account_id, actor=staff.id)
    except service.ZoikoNexBillingCycleError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    except zoikonex_adapter.ZoikoNexError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.post("/zoikonex/credit-notes", response_model=CreditNoteResponse)
def issue_credit_note(
    payload: IssueCreditNoteRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.issue_credit_note")),
):
    """SUPER_ADMIN only - corrects an over-billed ISSUED invoice. Same
    segregation-of-duties bar as run_billing_cycle - a real, money-adjacent
    ZoikoNex write."""
    try:
        return service.issue_invoice_credit_note(
            db, payload.account_id, payload.invoice_id,
            amount_minor_units=payload.amount_minor_units, reason=payload.reason, actor=staff.id,
        )
    except zoikonex_adapter.ZoikoNexError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.post("/zoikonex/debit-notes", response_model=DebitNoteResponse)
def issue_debit_note(
    payload: IssueDebitNoteRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.issue_debit_note")),
):
    """SUPER_ADMIN only - corrects an under-billed ISSUED invoice."""
    try:
        return service.issue_invoice_debit_note(
            db, payload.account_id, payload.invoice_id,
            amount_minor_units=payload.amount_minor_units, reason=payload.reason, actor=staff.id,
        )
    except zoikonex_adapter.ZoikoNexError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.post("/zoikonex/refunds", response_model=RefundResponse)
def refund_payment(
    payload: RefundPaymentRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.refund_payment")),
):
    """SUPER_ADMIN only - refunds a CAPTURED ZoikoNex payment. Currently
    always fails against a real ZoikoNex-side error (capture itself is
    broken there - see app.integrations.billing.zoikonex's docstring), not
    a bug in this endpoint - see refund_zoikonex_payment's docstring."""
    try:
        return service.refund_zoikonex_payment(
            db, payload.account_id, payload.payment_intent_id,
            amount_minor_units=payload.amount_minor_units, reason=payload.reason, actor=staff.id,
        )
    except zoikonex_adapter.ZoikoNexError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
