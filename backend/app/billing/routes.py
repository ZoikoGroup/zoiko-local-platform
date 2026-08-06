from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.billing import service
from app.billing.schemas import (
    ChangePlanRequest,
    PlanResponse,
    SimulatePaymentEventRequest,
    SubscriptionResponse,
    UsageSummaryResponse,
    ZoikoNexReconciliationSummary,
    ZoikoNexSyncEventResponse,
)
from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_staff_role
from app.numbering.identity.models import User
from app.staff.models import PlatformStaff, PlatformStaffRole

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


# --- Staff-only: mock ZoikoNex operations console ---
# There's no real ZoikoNex to send these webhooks for real yet - these
# routes exist so the graceful-degradation mechanism (Architecture doc §9)
# can actually be exercised and demonstrated end-to-end before a real
# connection exists. See app.integrations.billing.zoikonex's docstring.


@router.post("/zoikonex/simulate-payment-event", response_model=SubscriptionResponse)
def simulate_payment_event(
    payload: SimulatePaymentEventRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
):
    """SUPER_ADMIN only - this simulates a real billing-state change
    (Architecture doc §11 "segregation of duties for sensitive actions"),
    the same bar as KYC approve/reject."""
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
