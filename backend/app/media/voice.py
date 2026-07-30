"""
Voice Routing — Stage 3 groundwork, built ahead of Stage 1 finishing (see
CLAUDE.md's 2026-07-30 exception). No Account/Number model linkage yet, so
there is no way to know *whose* number is being called or to check
entitlements/compliance before acting. Do not treat this as a finished
feature — the TODOs below are the real remaining work, not decoration.

Only calls into app.integrations.telecom.twilio (the Provider Gateway) —
never imports the twilio SDK directly, per the Provider Gateway rule.
"""

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from app.integrations.telecom import twilio as telecom
from app.integrations.telecom.twilio import TelecomError

router = APIRouter(prefix="/media/voice", tags=["voice"])


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: str
    from_number: str = Field(alias="from")
    message: str = "This is a call from Zoiko Local."


@router.post("/incoming")
async def incoming_call():
    """Twilio hits this as a webhook when someone calls a number we own.
    TODO (blocked on Stage 1/2): look up which Account/business owns the
    called number, apply business-hours routing rules, and log the call via
    audit.service.log_event() once that module exists. Right now this always
    returns the same static greeting for every caller, on every number.
    """
    twiml = telecom.build_say_response(
        "Thanks for calling Zoiko Local. Call routing isn't built yet — this is placeholder audio."
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/outbound")
async def outbound_call(body: OutboundCallRequest):
    """Places an outbound call. NOT executed live yet in this project — the
    trial account owns zero numbers, so `from_number` has nothing valid to be.
    TODO (blocked on Stage 1): this should require an authenticated account,
    verify `from_number` is an Active number owned by that account, and log
    the action via audit.service.log_event() before calling Twilio.
    """
    twiml = telecom.build_say_response(body.message)
    try:
        return telecom.place_call(to=body.to, from_=body.from_number, twiml=twiml)
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/calls")
async def list_calls(limit: int = 20):
    try:
        return telecom.list_calls(limit=limit)
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("/calls/{call_sid}")
async def get_call(call_sid: str):
    try:
        return telecom.get_call(call_sid)
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
