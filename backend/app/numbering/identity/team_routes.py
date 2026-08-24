from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.billing.service import EntitlementRequiredError
from app.core.database import get_db
from app.core.deps import require_admin
from app.numbering.identity import service
from app.numbering.identity.models import User
from app.numbering.identity.schemas import TeamMemberAdd, UserResponse

router = APIRouter(prefix="/team", tags=["team"])


@router.post("/members", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def add_member(
    payload: TeamMemberAdd,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        member = service.add_team_member(
            db,
            account_id=current_user.account_id,
            email=payload.email,
            password=payload.password,
            role=payload.role,
            actor=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    # SeatQuotaExceededError no longer caught here - subclasses
    # EntitlementError, handled by the global entitlement_error_handler.
    except EntitlementRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": e.key, "current_plan": e.plan_code},
        )
    return member


@router.get("/members", response_model=list[UserResponse])
def list_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    return service.list_team_members(db, current_user.account_id)


@router.delete("/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        service.remove_team_member(
            db, account_id=current_user.account_id, user_id=user_id, actor=current_user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
