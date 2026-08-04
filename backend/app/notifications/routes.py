from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.notifications import service
from app.notifications.schemas import (
    NotificationDeliveryResponse,
    PushSubscribeRequest,
    PushSubscriptionResponse,
    PushUnsubscribeRequest,
    UnreadCountResponse,
)
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


@router.get("/me/unread-count", response_model=UnreadCountResponse)
def my_unread_notification_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return UnreadCountResponse(unread_count=service.count_unread_notifications(db, current_user.account_id))


@router.post("/me/{notification_id}/read", response_model=NotificationDeliveryResponse)
def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.mark_notification_read(db, current_user.account_id, notification_id)
    except service.NotificationNotFoundError:
        raise HTTPException(status_code=404, detail="Notification not found")


@router.post("/me/read-all", response_model=UnreadCountResponse)
def mark_all_notifications_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.mark_all_notifications_read(db, current_user.account_id)
    return UnreadCountResponse(unread_count=0)


@router.post("/push/subscribe", response_model=PushSubscriptionResponse)
def subscribe_to_push(
    payload: PushSubscribeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.subscribe_to_push(
        db, account_id=current_user.account_id, user_id=current_user.id,
        endpoint=payload.endpoint, p256dh=payload.p256dh, auth=payload.auth,
    )


@router.post("/push/unsubscribe", status_code=204)
def unsubscribe_from_push(
    payload: PushUnsubscribeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.unsubscribe_from_push(db, account_id=current_user.account_id, endpoint=payload.endpoint)
