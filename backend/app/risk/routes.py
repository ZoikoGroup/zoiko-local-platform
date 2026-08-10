from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_staff_role
from app.numbering.numbers.service import reactivate_numbers_for_account_by_staff
from app.risk import service
from app.risk.schemas import (
    AccountReinstateRequest,
    AccountRiskSummaryResponse,
    BlockedDestinationCreate,
    BlockedDestinationResponse,
)
from app.staff.models import PlatformStaff, PlatformStaffRole

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/blocked-destinations", response_model=list[BlockedDestinationResponse])
def list_blocked_destinations(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_blocked_destinations(db)


@router.post("/blocked-destinations", response_model=BlockedDestinationResponse, status_code=status.HTTP_201_CREATED)
def add_blocked_destination(
    payload: BlockedDestinationCreate,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
):
    try:
        return service.add_blocked_destination(db, prefix=payload.prefix, reason=payload.reason, actor=staff.id)
    except service.DestinationRuleConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/blocked-destinations/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_blocked_destination(
    rule_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
):
    service.remove_blocked_destination(db, rule_id, actor=staff.id)


@router.get("/accounts/{account_id}/score", response_model=AccountRiskSummaryResponse)
def get_account_risk_score(
    account_id: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-only view of an account's rolling risk score (Roadmap doc §13
    Risk Register: "account risk scoring") - the same signals and threshold
    that drive automatic suspension, visible before or after it fires."""
    return service.get_account_risk_summary(db, account_id)


@router.post("/accounts/{account_id}/reinstate", status_code=status.HTTP_200_OK)
def reinstate_account_numbers(
    account_id: str,
    payload: AccountReinstateRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(
        require_staff_role(PlatformStaffRole.SUPER_ADMIN, PlatformStaffRole.COMPLIANCE_OFFICER)
    ),
):
    """Reverses a risk-engine auto-suspension (or any suspension) after
    human review - reactivates every SUSPENDED number on the account."""
    numbers = reactivate_numbers_for_account_by_staff(db, account_id, staff_id=staff.id, reason=payload.reason)
    return {"reactivated": [n.e164 for n in numbers]}
