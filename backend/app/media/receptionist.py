"""
AI Receptionist — guarded Phase 1 scope (Roadmap §7): a single free-form
Gather captures the caller's name/company/reason/urgency in one utterance
(Twilio's own speech-to-text, not ours); if AI-processing consent is on file
for the account, Groq extracts structured qualification fields from that
utterance. No conversational back-and-forth, no binding commitments, no
pricing — pure message capture + optional enrichment + routing to a human
(if urgent and a team member is nominated via escalation_user_id) or a
polite close.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.integrations.telecom import twilio as telecom
from app.media import service as media_service
from app.media.models import ReceptionistUrgency
from app.numbering.identity.models import User

router = APIRouter(prefix="/media/receptionist", tags=["receptionist"])


class RouteReceptionistCallRequest(BaseModel):
    assigned_user_id: str | None = None


@router.post("/respond")
async def respond(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)

    call_sid = params.get("CallSid", "")
    to_number = params.get("To", "")
    from_number = params.get("From", "")
    transcript = params.get("SpeechResult", "")

    call = media_service.capture_receptionist_call(db, call_sid, to_number, from_number, transcript)
    if call is None:
        twiml = telecom.build_say_response("Thank you for calling. Goodbye.")
        return Response(content=twiml, media_type="application/xml")

    owner = media_service.find_number_owner(db, to_number)
    # Escalation requires a nominated team member (escalation_user_id), not
    # just a forwarding_number - that field is also used for plain
    # business-hours call forwarding, unrelated to receptionist urgency.
    should_escalate = (
        call.urgency == ReceptionistUrgency.HIGH
        and bool(owner.escalation_user_id)
        and bool(owner.forwarding_number)
    )

    if should_escalate:
        media_service.mark_receptionist_call_escalated(db, call.id, owner.escalation_user_id)
        status_callback_url = str(request.base_url) + "media/voice/status-callback"
        twiml = telecom.build_receptionist_reply_response(
            "Thanks — this sounds urgent, connecting you to someone now.",
            forward_to=owner.forwarding_number,
            status_callback_url=status_callback_url,
        )
    else:
        twiml = telecom.build_receptionist_reply_response(
            "Thank you — we've noted your message and someone will get back to you soon."
        )
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
            "assigned_user_id": c.assigned_user_id,
            "assigned_user_email": user_emails.get(c.assigned_user_id) if c.assigned_user_id else None,
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
    current_user: User = Depends(get_current_user),
):
    try:
        call = media_service.route_receptionist_call(db, current_user, call_id, payload.assigned_user_id)
    except media_service.ReceptionistCallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return {"id": call.id, "assigned_user_id": call.assigned_user_id}
