import uuid
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.consent.models import ConsentType
from app.consent.service import has_active_consent
from app.integrations.llm.groq import LLMError
from app.integrations.telecom import twilio as telecom
from app.integrations.video import livekit as video
from app.intelligence.service import qualify_caller
from app.media.models import (
    CallDirection,
    CallRecord,
    ReceptionistCall,
    ReceptionistUrgency,
    VideoSession,
    VideoSessionStatus,
    Voicemail,
)
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


class CallAuthorizationError(Exception):
    """Raised when the caller isn't allowed to place a call from the given number."""


class VideoSessionAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the given video session."""


class RecordingConsentRequiredError(Exception):
    """Raised when starting a video recording without active AI-processing
    consent on file - recording is opt-in and consent-gated, never automatic."""


async def verify_twilio_webhook(request: Request) -> dict:
    """Shared by every Twilio webhook route (voice, voicemail): rejects any
    request that doesn't carry a valid X-Twilio-Signature for this exact URL
    + form body — without this, anyone can POST fake call/recording events."""
    form = await request.form()
    params = dict(form)
    signature = request.headers.get("X-Twilio-Signature")
    if not telecom.validate_webhook_signature(str(request.url), params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio webhook signature")
    return params


def find_number_owner(db: Session, e164: str) -> PhoneNumber | None:
    return db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()


def is_within_business_hours(start: time, end: time, tz_name: str) -> bool:
    now_local = datetime.now(ZoneInfo(tz_name)).time()
    if start <= end:
        return start <= now_local <= end
    return now_local >= start or now_local <= end  # overnight range, e.g. 22:00-06:00


def should_forward_call(owner: PhoneNumber) -> bool:
    if not owner.forwarding_number:
        return False
    if owner.business_hours_start is None or owner.business_hours_end is None:
        return True  # forwarding configured with no schedule restriction = always forward
    return is_within_business_hours(owner.business_hours_start, owner.business_hours_end, owner.business_hours_timezone)


def record_call(
    db: Session,
    *,
    account_id: str | None,
    phone_number_id: str | None,
    direction: CallDirection,
    from_number: str,
    to_number: str,
    provider_call_sid: str | None,
    status: str,
    duration: int | None = None,
) -> CallRecord:
    call = CallRecord(
        account_id=account_id,
        phone_number_id=phone_number_id,
        direction=direction,
        from_number=from_number,
        to_number=to_number,
        provider_call_sid=provider_call_sid,
        status=status,
        duration=duration,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    log_event(
        db,
        actor_id=account_id,
        action=f"call.{direction.value}_recorded",
        target_type="call_record",
        target_id=call.id,
        metadata={"from": from_number, "to": to_number, "status": status},
    )
    return call


def place_outbound_call(
    db: Session, account_id: str, to: str, from_number: str, message: str, status_callback_url: str | None = None
) -> dict:
    owner = find_number_owner(db, from_number)
    if owner is None or owner.account_id != account_id or owner.status != PhoneNumberStatus.ACTIVE:
        raise CallAuthorizationError(f"{from_number} is not an active number owned by your account")

    twiml = telecom.build_say_response(message)
    result = telecom.place_call(to=to, from_=from_number, twiml=twiml, status_callback_url=status_callback_url)

    record_call(
        db,
        account_id=account_id,
        phone_number_id=owner.id,
        direction=CallDirection.OUTBOUND,
        from_number=from_number,
        to_number=to,
        provider_call_sid=result["sid"],
        status=result["status"],
    )
    return result


def update_call_status(db: Session, provider_call_sid: str, status: str, duration: int | None) -> CallRecord | None:
    """Applies a Twilio call-status callback (final status/duration) to the
    CallRecord written at call time — without this, records are frozen at
    their initial ringing/queued state forever."""
    call = db.query(CallRecord).filter(CallRecord.provider_call_sid == provider_call_sid).first()
    if call is None:
        return None

    call.status = status
    call.duration = duration
    db.commit()
    db.refresh(call)
    log_event(
        db, actor_id=call.account_id, action="call.status_updated",
        target_type="call_record", target_id=call.id, metadata={"status": status, "duration": duration},
    )
    return call


def record_call_recording(db: Session, provider_call_sid: str, recording_url: str, duration: int | None) -> CallRecord | None:
    """Applies a Twilio <Dial record> callback (see build_forward_response)
    to the CallRecord written at call time - arrives separately from, and
    usually after, the status-callback that sets the final call status."""
    call = db.query(CallRecord).filter(CallRecord.provider_call_sid == provider_call_sid).first()
    if call is None:
        return None

    call.recording_url = recording_url
    if duration is not None:
        call.duration = duration
    db.commit()
    db.refresh(call)
    log_event(
        db, actor_id=call.account_id, action="call.recorded",
        target_type="call_record", target_id=call.id, metadata={"duration": duration},
    )
    return call


def record_voicemail(
    db: Session,
    *,
    account_id: str,
    phone_number_id: str,
    from_number: str,
    recording_url: str,
    duration: int | None,
) -> Voicemail:
    voicemail = Voicemail(
        account_id=account_id,
        phone_number_id=phone_number_id,
        from_number=from_number,
        recording_url=recording_url,
        duration=duration,
    )
    db.add(voicemail)
    db.commit()
    db.refresh(voicemail)
    log_event(
        db, actor_id=account_id, action="voicemail.created",
        target_type="voicemail", target_id=voicemail.id, metadata={"from": from_number},
    )
    return voicemail


def list_account_voicemails(db: Session, account_id: str) -> list[Voicemail]:
    return (
        db.query(Voicemail)
        .filter(Voicemail.account_id == account_id)
        .order_by(Voicemail.created_at.desc())
        .all()
    )


def _find_account_video_session(db: Session, account_id: str, room_name: str) -> VideoSession:
    session = db.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    if session is None or session.account_id != account_id:
        raise VideoSessionAuthorizationError(f"{room_name} is not a video session owned by your account")
    return session


async def create_video_session(db: Session, account_id: str, host_user_id: str) -> VideoSession:
    room_name = f"zl-{uuid.uuid4().hex[:16]}"
    await video.create_room(room_name)

    session = VideoSession(
        account_id=account_id,
        host_user_id=host_user_id,
        room_name=room_name,
        status=VideoSessionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    log_event(
        db, actor_id=account_id, action="video.session.started",
        target_type="video_session", target_id=session.id, metadata={"room_name": room_name},
    )
    return session


async def end_video_session(db: Session, account_id: str, room_name: str) -> VideoSession:
    session = _find_account_video_session(db, account_id, room_name)

    # Stop any in-progress recording first - ending the room doesn't
    # automatically stop egress, and a dangling egress job would keep
    # recording nothing useful (or error out) once the room is gone.
    if session.recording_egress_id:
        await video.stop_room_recording(session.recording_egress_id)

    await video.end_room(room_name)

    session.status = VideoSessionStatus.ENDED
    session.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    log_event(
        db, actor_id=account_id, action="video.session.ended",
        target_type="video_session", target_id=session.id, metadata={"room_name": room_name},
    )
    return session


async def start_video_recording(db: Session, account_id: str, room_name: str) -> VideoSession:
    session = _find_account_video_session(db, account_id, room_name)
    if session.status != VideoSessionStatus.ACTIVE:
        raise VideoSessionAuthorizationError(f"{room_name} is not an active session")
    if session.recording_egress_id:
        raise VideoSessionAuthorizationError(f"{room_name} is already being recorded")
    if not has_active_consent(db, account_id, ConsentType.AI_PROCESSING):
        raise RecordingConsentRequiredError(
            "AI processing consent is required before recording video calls — "
            "grant it via POST /compliance/consent first"
        )

    egress_id = await video.start_room_recording(room_name)
    session.recording_egress_id = egress_id
    db.commit()
    db.refresh(session)
    log_event(
        db, actor_id=account_id, action="video.recording_started",
        target_type="video_session", target_id=session.id, metadata={"room_name": room_name},
    )
    return session


def generate_video_join_token(
    db: Session, account_id: str, room_name: str, identity: str, display_name: str
) -> str:
    session = _find_account_video_session(db, account_id, room_name)
    if session.status != VideoSessionStatus.ACTIVE:
        raise VideoSessionAuthorizationError(f"{room_name} is not an active session")
    return video.build_participant_token(room_name, identity, display_name)


def list_account_video_sessions(db: Session, account_id: str) -> list[VideoSession]:
    return (
        db.query(VideoSession)
        .filter(VideoSession.account_id == account_id)
        .order_by(VideoSession.created_at.desc())
        .all()
    )


def handle_video_webhook_event(db: Session, event) -> None:
    """Syncs real LiveKit room state back into VideoSession — without this,
    a room that closes because everyone left (rather than an explicit
    POST /rooms/{name}/end call) stays "active" in our DB forever."""
    if event.event == "egress_ended":
        _handle_egress_ended(db, event)
        return

    session = db.query(VideoSession).filter(VideoSession.room_name == event.room.name).first()
    if session is None:
        return

    if event.event == "room_finished" and session.status != VideoSessionStatus.ENDED:
        session.status = VideoSessionStatus.ENDED
        session.ended_at = datetime.now(timezone.utc)
        db.commit()
        log_event(
            db, actor_id=session.account_id, action="video.session.ended",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": event.room.name, "source": "livekit_webhook"},
        )
    elif event.event in ("participant_joined", "participant_left"):
        log_event(
            db, actor_id=session.account_id, action=f"video.{event.event}",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": event.room.name, "participant_identity": event.participant.identity},
        )


def _handle_egress_ended(db: Session, event) -> None:
    """Attaches the finished recording's file location once LiveKit's egress
    job completes - arrives asynchronously, well after the call itself ends."""
    egress_info = event.egress_info
    session = (
        db.query(VideoSession).filter(VideoSession.recording_egress_id == egress_info.egress_id).first()
    )
    if session is None:
        return

    if egress_info.file_results:
        session.recording_url = egress_info.file_results[0].location
        db.commit()
        log_event(
            db, actor_id=session.account_id, action="video.recording_completed",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": session.room_name, "egress_status": egress_info.status},
        )


def capture_receptionist_call(
    db: Session, call_sid: str, to_number: str, from_number: str, transcript: str
) -> ReceptionistCall | None:
    """Persists the caller's captured message, enriching it with structured
    fields via qualify_caller() when AI-processing consent is on file. A
    Groq failure degrades to a plain captured message (raw_transcript is
    always saved) rather than breaking the live call — the caller must
    still get a TwiML response either way.
    """
    owner = find_number_owner(db, to_number)
    if owner is None:
        return None

    qualification, model_version = None, None
    try:
        qualification, model_version = qualify_caller(db, owner.account_id, transcript)
    except LLMError:
        pass

    qualification = qualification or {}
    urgency_raw = qualification.get("urgency")
    urgency = ReceptionistUrgency(urgency_raw) if urgency_raw in ("low", "medium", "high") else None

    call = ReceptionistCall(
        account_id=owner.account_id,
        phone_number_id=owner.id,
        call_sid=call_sid,
        caller_number=from_number,
        raw_transcript=transcript,
        caller_name=qualification.get("name"),
        caller_company=qualification.get("company"),
        reason=qualification.get("reason"),
        urgency=urgency,
        model_version=model_version,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    log_event(
        db, actor_id=owner.account_id, action="receptionist.call_captured",
        target_type="receptionist_call", target_id=call.id,
        metadata={"urgency": urgency.value if urgency else None},
    )
    return call


def mark_receptionist_call_escalated(db: Session, receptionist_call_id: str) -> None:
    call = db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()
    if call is None:
        return
    call.escalated = True
    db.commit()
    log_event(
        db, actor_id=call.account_id, action="receptionist.call_escalated",
        target_type="receptionist_call", target_id=call.id, metadata={},
    )


def list_account_receptionist_calls(db: Session, account_id: str) -> list[ReceptionistCall]:
    return (
        db.query(ReceptionistCall)
        .filter(ReceptionistCall.account_id == account_id)
        .order_by(ReceptionistCall.created_at.desc())
        .all()
    )
