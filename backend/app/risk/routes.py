from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_staff_role
from app.risk import service
from app.risk.schemas import BlockedDestinationCreate, BlockedDestinationResponse
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
