from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.integrations.telecom.twilio import TelecomError
from app.media import service as media_service
from app.messaging import service
from app.messaging.models import MessagingChannel
from app.messaging.schemas import ConversationResponse, MessageResponse, SendMessageRequest
from app.messaging.service import (
    ChannelNotEnabledError,
    ConversationNotFoundError,
    NumberNotOwnedError,
    RecipientOptedOutError,
)
from app.numbering.identity.models import User

router = APIRouter(prefix="/messaging", tags=["messaging"])


def _send(payload: SendMessageRequest, db: Session, current_user: User, channel: MessagingChannel):
    try:
        return service.send_message(
            db, current_user.account_id, current_user.id, payload.phone_number_id, payload.to, payload.body, channel
        )
    except NumberNotOwnedError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"number {e} not found on this account") from e
    except ChannelNotEnabledError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{channel.value} is not approved/enabled for this number yet",
        ) from e
    except RecipientOptedOutError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{e} has opted out of messages") from e
    except TelecomError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.post("/whatsapp/send", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_whatsapp(
    payload: SendMessageRequest, db: Session = Depends(get_db), current_user: User = Depends(require_writer)
):
    return _send(payload, db, current_user, MessagingChannel.WHATSAPP)


@router.post("/sms/send", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def send_sms(
    payload: SendMessageRequest, db: Session = Depends(get_db), current_user: User = Depends(require_writer)
):
    return _send(payload, db, current_user, MessagingChannel.SMS)


@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.list_conversations(db, current_user.account_id)


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
def list_messages(conversation_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return service.list_messages(db, current_user.account_id, conversation_id)
    except ConversationNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


# --- Twilio webhooks ---

webhook_router = APIRouter(prefix="/messaging", tags=["messaging"])


@webhook_router.post("/whatsapp/incoming")
async def whatsapp_incoming(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)
    service.record_inbound_whatsapp_message(
        db,
        whatsapp_to=params.get("To", ""),
        whatsapp_from=params.get("From", ""),
        body=params.get("Body", ""),
        provider_message_sid=params.get("MessageSid", ""),
    )
    return Response(status_code=204)


@webhook_router.post("/whatsapp/status")
async def whatsapp_status(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)
    service.update_message_status(db, params.get("MessageSid", ""), params.get("MessageStatus", ""))
    return Response(status_code=204)


@webhook_router.post("/sms/incoming")
async def sms_incoming(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)
    service.record_inbound_sms_message(
        db,
        to_number=params.get("To", ""),
        from_number=params.get("From", ""),
        body=params.get("Body", ""),
        provider_message_sid=params.get("MessageSid", ""),
    )
    return Response(status_code=204)


@webhook_router.post("/sms/status")
async def sms_status(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)
    service.update_message_status(db, params.get("MessageSid", ""), params.get("MessageStatus", ""))
    return Response(status_code=204)
