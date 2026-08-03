from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.notifications import service
from app.notifications.schemas import NotificationDeliveryResponse
from app.numbering.identity.models import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/me", response_model=list[NotificationDeliveryResponse])
def my_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The doc's "Communications History" trust surface, sized for what
    this platform actually sends today - every email delivery attempt for
    the current account, most recent first."""
    return service.list_account_notifications(db, current_user.account_id)
