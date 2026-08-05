from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.billing import service
from app.billing.schemas import ChangePlanRequest, PlanResponse, SubscriptionResponse, UsageSummaryResponse
from app.core.database import get_db
from app.core.deps import get_current_user, require_admin
from app.numbering.identity.models import User

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
    decision in this app. Purely local plan reassignment - no payment is
    collected or processed (see Subscription's docstring); it just changes
    which entitlement limits apply, in anticipation of a real ZoikoNex
    connection that doesn't exist yet."""
    try:
        return service.change_plan(db, current_user.account_id, payload.plan_code, actor=current_user.id)
    except service.PlanNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/usage-summary", response_model=UsageSummaryResponse)
def usage_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.get_usage_summary(db, current_user.account_id)
