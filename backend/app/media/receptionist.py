"""
AI Receptionist — Roadmap §7 scope: a free-form Gather captures the caller's
name/company/reason/urgency in one utterance (Twilio's own speech-to-text,
not ours); if AI-processing consent is on file for the account, Groq
extracts structured qualification fields from that utterance. If neither a
name nor a reason was understood, one bounded follow-up question is asked
(never more than one - see media.service.needs_receptionist_followup) before
finalizing. Once the message is as complete as it's going to get: a genuinely
urgent call (escalation_user_id + forwarding_number both configured) is
forwarded to a human immediately; otherwise the caller is offered a small
DTMF menu to accept the message as-is or request a callback window (Roadmap
§7 self-service booking - a caller-selected ASAP/today/tomorrow preference,
not a real calendar booking - see ReceptionistCallbackWindow's docstring).
Still no binding commitments, no pricing - this is guarded message capture
plus routing, not an open-ended conversational agent.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.integrations.telecom import twilio as telecom
from app.media import service as media_service
from app.media.models import ReceptionistCallbackWindow, ReceptionistCall, ReceptionistUrgency
from app.numbering.identity.models import User
from app.numbering.numbers.models import PhoneNumber

router = APIRouter(prefix="/media/receptionist", tags=["receptionist"])

_CALLBACK_WINDOW_BY_DIGIT = {
    "1": ReceptionistCallbackWindow.ASAP,
    "2": ReceptionistCallbackWindow.TODAY,
    "3": ReceptionistCallbackWindow.TOMORROW,
}

_CALLBACK_WINDOW_PHRASE = {
    ReceptionistCallbackWindow.ASAP: "as soon as possible",
    ReceptionistCallbackWindow.TODAY: "later today",
    ReceptionistCallbackWindow.TOMORROW: "tomorrow",
}

_GOODBYE_TWIML = telecom.build_say_response("Thank you for calling. Goodbye.")


class RouteReceptionistCallRequest(BaseModel):
    assigned_user_id: str | None = None


class EditReceptionistCallSummaryRequest(BaseModel):
    summary: str


def _finish_capture_and_respond(request: Request, db: Session, call: ReceptionistCall, owner: PhoneNumber) -> Response:
    """Shared close-out for /respond and /respond-followup once the message
    is as complete as it's going to get. Escalation requires a nominated
    team member (escalation_user_id) AND a real number to actually dial
    them on (escalation_phone_number) - prefers that dedicated field, falls
    back to forwarding_number for numbers configured before it existed.
    Confirmed live (2026-08-22): dialing forwarding_number unconditionally
    used to be the ONLY option, which meant a business running AI
    Receptionist as its primary mode (forwarding off) had no way to set an
    escalation destination without forwarding_number's mere presence also
    flipping should_forward_call() to true and hijacking every inbound
    call into always-forward mode. A genuinely urgent call is forwarded
    immediately, never delayed behind the callback menu below."""
    escalation_number = owner.escalation_phone_number or owner.forwarding_number
    should_escalate = (
        call.urgency == ReceptionistUrgency.HIGH
        and bool(owner.escalation_user_id)
        and bool(escalation_number)
    )

    if should_escalate:
        media_service.mark_receptionist_call_escalated(db, call.id, owner.escalation_user_id)
        # Dedicated fallback (real gap fix - see escalation_fallback and
        # twilio.build_receptionist_reply_response's docstring): the generic
        # /media/voice/status-callback always returns 204 with no TwiML,
        # which silently hung up an urgent call the moment the escalation
        # target didn't pick up. This route inspects DialCallStatus and
        # falls back to voicemail instead, so the message is never lost.
        fallback_action_url = (
            str(request.base_url) + f"media/receptionist/escalation-fallback?receptionist_call_id={call.id}"
        )
        # Real gap fix: an escalated call is a live two-way conversation
        # with no other capture mechanism of its own (unlike the
        # pre-escalation Gather utterance, already on the ReceptionistCall
        # row) - same AI_PROCESSING consent gate as a plain forwarded call
        # (should_record_forwarded_call), reusing /media/voice/recording-
        # callback since this is still the same outer CallSid throughout.
        recording_callback_url = (
            str(request.base_url) + "media/voice/recording-callback"
            if media_service.should_record_forwarded_call(db, call.account_id)
            else None
        )
        twiml = telecom.build_receptionist_reply_response(
            "Thanks — this sounds urgent, connecting you to someone now.",
            forward_to=escalation_number,
            fallback_action_url=fallback_action_url,
            recording_callback_url=recording_callback_url,
        )
        return Response(content=twiml, media_type="application/xml")

    action_url = str(request.base_url) + f"media/receptionist/menu-select?receptionist_call_id={call.id}"
    twiml = telecom.build_dtmf_menu_response(
        "Thank you. If that message is complete, press 1. To request a callback, press 2.", action_url,
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/respond")
async def respond(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)

    call_sid = params.get("CallSid", "")
    to_number = params.get("To", "")
    from_number = params.get("From", "")
    transcript = params.get("SpeechResult", "")

    call = media_service.capture_receptionist_call(db, call_sid, to_number, from_number, transcript)
    if call is None:
        return Response(content=_GOODBYE_TWIML, media_type="application/xml")

    owner = media_service.find_number_owner(db, to_number)

    if media_service.needs_receptionist_followup(call):
        action_url = str(request.base_url) + f"media/receptionist/respond-followup?receptionist_call_id={call.id}"
        twiml = telecom.build_gather_response(
            "Sorry, could you tell me your name and the reason for your call?", action_url,
        )
        return Response(content=twiml, media_type="application/xml")

    return _finish_capture_and_respond(request, db, call, owner)


@router.post("/respond-followup")
async def respond_followup(request: Request, db: Session = Depends(get_db)):
    """Twilio's action URL for the one bounded follow-up Gather (see
    respond's docstring) - `SpeechResult` is absent when the caller said
    nothing before the gather timed out, which record_receptionist_followup
    treats as "finalize with whatever the first Gather already captured"."""
    params = await media_service.verify_twilio_webhook(request)
    receptionist_call_id = request.query_params.get("receptionist_call_id", "")

    call = media_service.get_receptionist_call(db, receptionist_call_id)
    if call is None:
        return Response(content=_GOODBYE_TWIML, media_type="application/xml")

    call = media_service.record_receptionist_followup(db, call, params.get("SpeechResult", ""))

    owner = media_service.find_number_owner(db, params.get("To", ""))
    if owner is None:
        return Response(content=_GOODBYE_TWIML, media_type="application/xml")

    return _finish_capture_and_respond(request, db, call, owner)


@router.post("/menu-select")
async def menu_select(request: Request):
    """Twilio's action URL for the post-capture DTMF menu (see
    _finish_capture_and_respond) - `Digits` is absent on a timeout/no
    input, which falls through to the same close-out as pressing 1."""
    params = await media_service.verify_twilio_webhook(request)
    receptionist_call_id = request.query_params.get("receptionist_call_id", "")
    digit = params.get("Digits", "")

    if digit == "2":
        action_url = str(request.base_url) + f"media/receptionist/callback-select?receptionist_call_id={receptionist_call_id}"
        twiml = telecom.build_dtmf_menu_response(
            "Press 1 for as soon as possible, 2 for later today, or 3 for tomorrow.", action_url,
        )
        return Response(content=twiml, media_type="application/xml")

    twiml = telecom.build_say_response(
        "Thank you — we've noted your message and someone will get back to you soon."
    )
    return Response(content=twiml, media_type="application/xml")


@router.post("/callback-select")
async def callback_select(request: Request, db: Session = Depends(get_db)):
    """Twilio's action URL for the callback-window sub-menu (Roadmap §7
    self-service booking). An unrecognized digit or a timeout still
    records the callback request (the caller already pressed 2 to get
    here) - just with no specific window."""
    params = await media_service.verify_twilio_webhook(request)
    receptionist_call_id = request.query_params.get("receptionist_call_id", "")
    digit = params.get("Digits", "")

    call = media_service.get_receptionist_call(db, receptionist_call_id)
    if call is None:
        return Response(content=_GOODBYE_TWIML, media_type="application/xml")

    window = _CALLBACK_WINDOW_BY_DIGIT.get(digit)
    media_service.record_receptionist_callback_request(db, call, window)

    window_phrase = _CALLBACK_WINDOW_PHRASE.get(window, "soon")
    twiml = telecom.build_say_response(f"Thanks — we'll call you back {window_phrase}. Goodbye.")
    return Response(content=twiml, media_type="application/xml")


@router.post("/escalation-fallback")
async def escalation_fallback(request: Request, db: Session = Depends(get_db)):
    """Twilio's action URL for an escalated (HIGH-urgency) call's <Dial> to
    the human (see _finish_capture_and_respond and twilio.
    build_receptionist_reply_response's docstring for the bug this fixes).
    `DialCallStatus` is "completed" for a call the human actually answered
    and has now ended normally - nothing further to do there. Any other
    status (no-answer, busy, failed) means the escalation target never
    picked up - falls back to a real voicemail (media.voicemail.
    recording_complete, same as every other voicemail path in this
    codebase) so the caller's message is preserved instead of the call
    just silently ending."""
    params = await media_service.verify_twilio_webhook(request)
    if params.get("DialCallStatus") == "completed":
        return Response(content=telecom.build_empty_response(), media_type="application/xml")

    receptionist_call_id = request.query_params.get("receptionist_call_id", "")
    call = media_service.get_receptionist_call(db, receptionist_call_id)
    if call is not None:
        media_service.mark_receptionist_escalation_missed(db, call.id)

    callback_url = str(request.base_url) + "media/voicemail/recording-complete"
    twiml = telecom.build_record_response(callback_url)
    return Response(content=twiml, media_type="application/xml")


@router.get("/calls")
async def list_receptionist_calls(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    calls = media_service.list_account_receptionist_calls(db, current_user)
    # One lookup for the whole list rather than per-row - assignee email is
    # display-only (the frontend already knows ids from /team/members), so
    # this avoids an N+1 without needing a relationship/join on the model.
    user_emails = {u.id: u.email for u in db.query(User).filter(User.account_id == current_user.account_id).all()}
    return [
        {
            "id": c.id,
            "call_sid": c.call_sid,
            "caller_number": c.caller_number,
            "raw_transcript": c.raw_transcript,
            "caller_name": c.caller_name,
            "caller_company": c.caller_company,
            "reason": c.reason,
            "summary": c.summary,
            "urgency": c.urgency.value if c.urgency else None,
            "escalated": c.escalated,
            "guardrail_flags": c.guardrail_flags,
            "is_likely_spam": c.is_likely_spam,
            "spam_reason": c.spam_reason,
            "callback_preference": c.callback_preference,
            "callback_requested": c.callback_requested,
            "callback_window": c.callback_window.value if c.callback_window else None,
            "assigned_user_id": c.assigned_user_id,
            "assigned_user_email": user_emails.get(c.assigned_user_id) if c.assigned_user_id else None,
            "original_summary": c.original_summary,
            "edited_at": c.edited_at,
            "model_version": c.model_version,
            "created_at": c.created_at,
        }
        for c in calls
    ]


@router.post("/calls/{call_id}/assign")
async def assign_receptionist_call(
    call_id: str,
    payload: RouteReceptionistCallRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        call = media_service.route_receptionist_call(db, current_user, call_id, payload.assigned_user_id)
    except media_service.ReceptionistCallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"id": call.id, "assigned_user_id": call.assigned_user_id}


@router.patch("/calls/{call_id}")
async def edit_receptionist_call(
    call_id: str,
    payload: EditReceptionistCallSummaryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        call = media_service.edit_receptionist_call_summary(db, current_user, call_id, payload.summary)
    except media_service.ReceptionistCallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"id": call.id, "summary": call.summary, "original_summary": call.original_summary, "edited_at": call.edited_at}
