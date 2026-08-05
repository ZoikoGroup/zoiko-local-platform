from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.notifications import service
from app.notifications.schemas import (
    NotificationDeliveryResponse,
    PushSubscribeRequest,
    PushSubscriptionResponse,
    PushUnsubscribeRequest,
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
    current_user: User = Depends(get_current_user),
):
    try:
        return service.mark_notification_read(db, current_user.account_id, notification_id)
    except service.NotificationAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = service.mark_all_notifications_read(db, current_user.account_id)
    return {"marked_read": count}


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
