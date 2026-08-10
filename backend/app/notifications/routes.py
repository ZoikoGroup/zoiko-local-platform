from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_writer
from app.notifications import service
from app.notifications.schemas import (
    NotificationDeliveryResponse,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdate,
    NotificationSuppressionResponse,
    PushSubscribeRequest,
    PushSubscriptionResponse,
    PushUnsubscribeRequest,
)
from app.numbering.identity.models import User
from app.staff.models import PlatformStaff

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
            disabled_domains=fields.get("disabled_domains"),
        )
    except service.InvalidTimezoneError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe(token: str = Query(...), db: Session = Depends(get_db)):
    """RFC 8058 one-click unsubscribe target - opened directly from an
    email client, not the SPA, so it returns a plain confirmation page
    rather than JSON. No auth required: the signed token itself proves
    which address/domain to suppress (see
    service._create_unsubscribe_token)."""
    success, message = service.unsubscribe_via_token(db, token)
    return HTMLResponse(
        f"<!doctype html><html><body style='font-family: sans-serif; max-width: 480px; margin: 4rem auto;'>"
        f"<p>{message}</p></body></html>",
        status_code=200 if success else 400,
    )


@router.post("/webhooks/resend", status_code=status.HTTP_204_NO_CONTENT)
async def resend_webhook(request: Request, db: Session = Depends(get_db)):
    """Receives Resend's bounce/complaint/delivered/clicked events - see
    service.handle_resend_webhook's docstring for why this is real,
    signature-verifying code that hasn't been exercised against a live
    Resend account yet."""
    payload = await request.body()
    try:
        service.handle_resend_webhook(
            db,
            payload=payload,
            svix_id=request.headers.get("svix-id", ""),
            svix_timestamp=request.headers.get("svix-timestamp", ""),
            svix_signature=request.headers.get("svix-signature", ""),
        )
    except service.WebhookSignatureError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e)) from e


@router.get("/staff/suppressions", response_model=list[NotificationSuppressionResponse])
def list_suppressions(
    recipient_email: str | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-facing view of the central suppression list (doc §4.2:
    suppression is "audited like deliveries") - any staff role can look
    up why a given address stopped receiving mail."""
    return service.list_suppressions(db, recipient_email)
