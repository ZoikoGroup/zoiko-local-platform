from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_capability
from app.numbering.identity.models import User
from app.staff.models import PlatformStaff
from app.usage import service
from app.usage.models import UsageDisputeStatus
from app.usage.schemas import (
    AIUsageRateResponse,
    CallingRateResponse,
    NumberRateResponse,
    OpenUsageDisputeRequest,
    ResolveUsageDisputeRequest,
    UsageDisputeResponse,
    UsageEventResponse,
)

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=list[UsageEventResponse])
def list_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    # Owner/Admin only - usage feeds billing, same sensitivity as consent/
    # compliance actions elsewhere in this codebase.
    return service.list_account_usage(db, current_user.account_id)


@router.get("/calling-rates", response_model=list[CallingRateResponse])
def list_calling_rates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Any authenticated member can see pricing before placing a call -
    # not an Owner/Admin-only view like the usage ledger itself.
    return service.list_calling_rates(db)


@router.get("/number-rates", response_model=list[NumberRateResponse])
def list_number_rates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Same visibility bar as calling-rates above - any authenticated member
    # can see what an additional number costs before buying one.
    return service.list_number_rates(db)


@router.get("/ai-usage-rate", response_model=AIUsageRateResponse | None)
def get_ai_usage_rate(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_ai_usage_rate(db)


@router.post("/disputes", response_model=UsageDisputeResponse, status_code=status.HTTP_201_CREATED)
def open_usage_dispute(
    payload: OpenUsageDisputeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Owner/Admin only - same sensitivity bar as the usage ledger itself."""
    try:
        return service.open_usage_dispute(
            db, account_id=current_user.account_id, usage_event_id=payload.usage_event_id,
            reason=payload.reason, raised_by=current_user.id,
        )
    except service.UsageEventNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/disputes", response_model=list[UsageDisputeResponse])
def list_own_usage_disputes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return service.list_account_usage_disputes(db, current_user.account_id)


@router.get("/disputes/staff", response_model=list[UsageDisputeResponse])
def list_usage_disputes_staff(
    dispute_status: UsageDisputeStatus | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-only queue view - any staff role can view (diagnostic, same
    posture as the fraud-case queue); resolving one is gated below."""
    return service.list_usage_disputes(db, dispute_status)


@router.post("/disputes/{dispute_id}/resolve", response_model=UsageDisputeResponse)
def resolve_usage_dispute(
    dispute_id: str,
    payload: ResolveUsageDisputeRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.resolve_usage_dispute")),
):
    try:
        status_enum = UsageDisputeStatus(payload.status)
    except ValueError:
        valid = ", ".join(s.value for s in (UsageDisputeStatus.RESOLVED_ADJUSTED, UsageDisputeStatus.RESOLVED_DENIED))
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"status must be one of: {valid}")
    try:
        return service.resolve_usage_dispute(
            db, dispute_id, actor=staff.id, status=status_enum, notes=payload.notes,
            new_estimated_cost_cents=payload.new_estimated_cost_cents,
        )
    except service.UsageDisputeNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (service.UsageDisputeAlreadyResolvedError, service.InvalidUsageDisputeResolutionError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
