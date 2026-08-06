"""
Voice Routing — wired to real Account/Number data (Stage 3). Inbound webhooks
are signature-verified and every call (recognized or not) is persisted via
media.service.record_call(); outbound calls require an authenticated account
that actually owns the `from_number` being used.

Only calls into app.integrations.telecom.twilio (the Provider Gateway) —
never imports the twilio SDK directly, per the Provider Gateway rule.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.billing.service import BillingSuspendedError
from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.integrations.telecom import twilio as telecom
from app.integrations.telecom.twilio import TelecomError
from app.media import service as media_service
from app.media.models import CallDirection
from app.numbering.identity.models import User
from app.numbering.numbers import service as numbers_service
from app.risk import service as risk_service

router = APIRouter(prefix="/media/voice", tags=["voice"])


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: str
    from_number: str = Field(alias="from")
    message: str = "This is a call from Zoiko Local."


@router.post("/incoming")
async def incoming_call(request: Request, db: Session = Depends(get_db)):
    """Twilio hits this as a webhook when someone calls a number we own. If a
    forwarding_number is configured (and current time is within any configured
    business hours), the call is forwarded live; otherwise it goes to
    voicemail. The call is attributed to the owning account (or logged as
    unrecognized) via the persisted CallRecord regardless of which branch runs.
    """
    params = await media_service.verify_twilio_webhook(request)

    to_number = params.get("To", "")
    from_number = params.get("From", "")
    owner = media_service.find_number_owner(db, to_number)

    media_service.record_call(
        db,
        account_id=owner.account_id if owner else None,
        phone_number_id=owner.id if owner else None,
        direction=CallDirection.INBOUND,
        from_number=from_number,
        to_number=to_number,
        provider_call_sid=params.get("CallSid"),
        status=params.get("CallStatus", "unknown"),
    )

    if owner is not None and media_service.should_forward_call(owner):
        status_callback_url = str(request.base_url) + "media/voice/status-callback"
        fallback_action_url = str(request.base_url) + "media/voice/forward-fallback"
        recording_callback_url = (
            str(request.base_url) + "media/voice/recording-callback"
            if media_service.should_record_forwarded_call(db, owner.account_id)
            else None
        )
        # Enhanced business routing: ring every configured destination
        # simultaneously if a ring group is set, otherwise fall back to
        # the plain single forwarding_number - identical behavior to
        # before this feature existed for any number that never sets one.
        ring_group = numbers_service.list_ring_group(db, to_number)
        destinations = [d.destination_number for d in ring_group] or [owner.forwarding_number]
        twiml = telecom.build_ring_group_response(
            destinations, fallback_action_url, status_callback_url, recording_callback_url
        )
    elif owner is not None and owner.ai_receptionist_enabled:
        action_url = str(request.base_url) + "media/receptionist/respond"
        twiml = telecom.build_gather_response(
            "Thanks for calling. You're speaking with an automated assistant, not a person. "
            "Please tell us your name, company, the reason for your call, and whether "
            "it's urgent, after the tone.",
            action_url,
        )
    elif owner is not None:
        callback_url = str(request.base_url) + "media/voicemail/recording-complete"
        twiml = telecom.build_record_response(callback_url)
    else:
        twiml = telecom.build_say_response(
            "Thanks for calling Zoiko Local. This number isn't recognized."
        )
    return Response(content=twiml, media_type="application/xml")


@router.post("/outbound")
async def outbound_call(
    body: OutboundCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    status_callback_url = str(request.base_url) + "media/voice/status-callback"
    try:
        return media_service.place_outbound_call(
            db, current_user, body.to, body.from_number, body.message, status_callback_url
        )
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except BillingSuspendedError as e:
        raise HTTPException(status_code=402, detail=str(e)) from e
    except risk_service.DestinationBlockedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except risk_service.VelocityLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/status-callback")
async def status_callback(request: Request, db: Session = Depends(get_db)):
    """Twilio posts here on call completion (outbound calls that were placed
    with a status_callback_url, and inbound calls to numbers purchased while
    PUBLIC_BASE_URL was configured) — see twilio.buy_number()/place_call()."""
    params = await media_service.verify_twilio_webhook(request)
    duration_raw = params.get("CallDuration")
    media_service.update_call_status(
        db,
        provider_call_sid=params.get("CallSid", ""),
        status=params.get("CallStatus", "unknown"),
        duration=int(duration_raw) if duration_raw else None,
    )
    return Response(status_code=204)


@router.post("/forward-fallback")
async def forward_fallback(request: Request, db: Session = Depends(get_db)):
    """Twilio requests this as the forwarded/ring-group <Dial>'s `action`
    URL once the dial resolves (see telecom.build_ring_group_response).
    `DialCallStatus` is "completed" for a call that was actually answered
    and has now ended normally - nothing further to do there. Any other
    status (no-answer, busy, failed) means nobody picked up, so the
    caller is routed to voicemail instead of just hearing silence -
    "overflow handling" (Architecture doc Phase 2), previously missing
    for both the single-forwarding-number and ring-group cases."""
    params = await media_service.verify_twilio_webhook(request)
    if params.get("DialCallStatus") == "completed":
        return Response(content=telecom.build_empty_response(), media_type="application/xml")

    callback_url = str(request.base_url) + "media/voicemail/recording-complete"
    twiml = telecom.build_record_response(callback_url)
    return Response(content=twiml, media_type="application/xml")


@router.post("/recording-callback")
async def recording_callback(request: Request, db: Session = Depends(get_db)):
    """Twilio posts here once a forwarded call's recording (see
    build_forward_response's record="record-from-answer-dual") finishes
    processing - separate from, and usually after, /status-callback."""
    params = await media_service.verify_twilio_webhook(request)
    duration_raw = params.get("RecordingDuration")
    media_service.record_call_recording(
        db,
        provider_call_sid=params.get("CallSid", ""),
        recording_url=params.get("RecordingUrl", ""),
        duration=int(duration_raw) if duration_raw else None,
    )
    return Response(status_code=204)


@router.get("/calls")
async def list_calls(
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    calls = media_service.list_account_calls(db, current_user, limit)
    return [
        {
            "id": c.id,
            "sid": c.provider_call_sid,
            "status": c.status,
            "to": c.to_number,
            "from": c.from_number,
            "direction": c.direction.value,
            "duration": c.duration,
            "recording_url": c.recording_url,
            "is_suspected_spam": c.is_suspected_spam,
            "created_at": c.created_at,
        }
        for c in calls
    ]


@router.get("/calls/{call_sid}")
async def get_call(
    call_sid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        media_service.assert_can_access_call(db, current_user, call_sid)
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    try:
        return telecom.get_call(call_sid)
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
