from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.notifications import service
from app.notifications.schemas import (
    NotificationDeliveryResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
)
from app.numbering.identity.models import User

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/me", response_model=list[NotificationDeliveryResponse])
def my_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The doc's "Communications History" trust surface, sized for what
    this platform actually sends today - every email/SMS delivery attempt
    for the current account, most recent first. Also backs the dashboard's
    in-app notification bell."""
    return service.list_account_notifications(db, current_user.account_id)


@router.post("/{notification_id}/read", response_model=NotificationDeliveryResponse)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.mark_notification_read(db, current_user.account_id, notification_id)
    except service.NotificationAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    count = service.mark_all_notifications_read(db, current_user.account_id)
    return {"marked_read": count}


@router.get("/preferences", response_model=NotificationPreferenceResponse)
def get_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_or_create_preference(db, current_user.account_id)


@router.put("/preferences", response_model=NotificationPreferenceResponse)
def put_preferences(
    payload: NotificationPreferenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    fields = payload.model_dump(exclude_unset=True)
    try:
        return service.update_preference(
            db,
            current_user.account_id,
            transactional_enabled=fields.get("transactional_enabled"),
            sms_enabled=fields.get("sms_enabled"),
            quiet_hours_start=fields["quiet_hours_start"] if "quiet_hours_start" in fields else ...,
            quiet_hours_end=fields["quiet_hours_end"] if "quiet_hours_end" in fields else ...,
            quiet_hours_timezone=fields.get("quiet_hours_timezone"),
        )
    except service.InvalidTimezoneError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
