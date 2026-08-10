from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin
from app.numbering.identity.models import User
from app.usage import service
from app.usage.schemas import CallingRateResponse, UsageEventResponse

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
