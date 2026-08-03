from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_admin
from app.numbering.identity.models import User
from app.usage import service
from app.usage.schemas import UsageEventResponse

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=list[UsageEventResponse])
def list_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    # Owner/Admin only - usage feeds billing, same sensitivity as consent/
    # compliance actions elsewhere in this codebase.
    return service.list_account_usage(db, current_user.account_id)
