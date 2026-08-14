import uuid
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing import service as billing_service
from app.consent.models import ConsentType
from app.consent.service import has_active_consent
from app.events.service import (
    publish_call_ended,
    publish_call_started,
    publish_video_room_created,
    publish_video_room_ended,
    publish_voicemail_created,
)
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.llm.groq import LLMError
from app.integrations.storage.s3 import StorageError, generate_presigned_url
from app.integrations.telecom import twilio as telecom
from app.integrations.video import livekit as video
from app.intelligence.guardrails import check_for_disallowed_commitments
from app.intelligence.service import qualify_caller
from app.notifications.service import (
    notify_high_risk_destination_blocked,
    notify_video_guest_waiting,
    notify_voicemail_received,
)
from app.media.models import (
    TERMINAL_CALL_STATUSES,
    CallDirection,
    CallRecord,
    ConnectionQuality,
    ReceptionistCall,
    ReceptionistUrgency,
    VideoParticipantSession,
    VideoSession,
    VideoSessionStatus,
    VideoWaitingGuest,
    VideoWaitingGuestStatus,
    Voicemail,
)
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus
from app.numbering.numbers.service import NumberConflictError, assert_number_access, assigned_number_ids
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active
from app.retention.service import PURGED_MARKER
from app.risk import service as risk_service
from app.usage import service as usage_service


class CallAuthorizationError(Exception):
    """Raised when the caller isn't allowed to place a call from the given number."""


class VideoSessionAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the given video session."""


class ReceptionistCallAuthorizationError(Exception):
    """Raised when routing a receptionist call the caller can't access, or
    to a user who isn't a team member on the same account."""


class RecordingConsentRequiredError(Exception):
    """Raised when starting a video recording without active AI-processing
    consent on file - recording is opt-in and consent-gated, never automatic."""


class ConfidentialModeRecordingBlockedError(Exception):
    """Raised when trying to record a session created with confidential=True.
    Blocked unconditionally, regardless of consent status - confidential mode
    is a stronger guarantee than "consent not yet granted", so it's checked
    first and consent is never even considered."""


class WaitingGuestNotFoundError(Exception):
    """Raised when a waiting-room request id doesn't exist for the given room."""


class WaitingGuestNoLongerPendingError(Exception):
    """Raised when a host tries to admit/deny a request that's already been
    resolved (admitted/denied earlier - e.g. a double-click) or expired -
    there's nothing meaningful left to decide."""


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


def should_record_forwarded_call(db: Session, account_id: str) -> bool:
    """Architecture doc §2.2: "Recording: off by default. Where enabled, it
    must be consented..." - reuses the same AI_PROCESSING consent record the
    video-recording feature gates on, rather than recording every forwarded
    call unconditionally the moment forwarding_number is configured."""
    return has_active_consent(db, account_id, ConsentType.AI_PROCESSING)


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
    is_suspected_spam = (
        direction == CallDirection.INBOUND
        and risk_service.is_suspected_spam_caller(db, from_number, candidate_account_id=account_id)
    )
    call = CallRecord(
        account_id=account_id,
        phone_number_id=phone_number_id,
        direction=direction,
        from_number=from_number,
        to_number=to_number,
        provider_call_sid=provider_call_sid,
        status=status,
        duration=duration,
        is_suspected_spam=is_suspected_spam,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    _invalidate_calls_cache(account_id)
    log_event(
        db,
        actor_id=account_id,
        action=f"call.{direction.value}_recorded",
        target_type="call_record",
        target_id=call.id,
        metadata={"from": from_number, "to": to_number, "status": status},
    )
    if account_id and provider_call_sid:
        publish_call_started(
            account_id, call_sid=provider_call_sid, from_number=from_number, to_number=to_number,
            direction=direction.value,
        )
    return call


def _dispatch_outbound_call(
    db: Session, *, account_id: str, account_email: str, owner: PhoneNumber, to: str, from_number: str,
    message: str, status_callback_url: str | None,
) -> dict:
    """Shared core of place_outbound_call and place_outbound_call_for_account
    - everything after "is this number really available to the caller" is
    identical for both a logged-in user and a public API key."""
    # Commercial Billing Operating Standard doc §32.1 - checked first,
    # alongside (not instead of) the per-account risk gates below; this one
    # is platform-wide and manually triggered, not account-specific.
    assert_kill_switch_not_active(db, KillSwitchScope.OUTBOUND_CALLING)
    # Production Readiness Standard Table 15's "Tenant" kill-switch scope -
    # halts just this one account's outbound calling without suspending it.
    risk_service.assert_account_kill_switch_not_active(db, account_id, KillSwitchScope.OUTBOUND_CALLING)

    # Graceful degradation (Architecture doc §9) - outbound calling pauses
    # once a payment grace period expires; inbound calls are deliberately
    # never gated this way (see billing_service.assert_billing_not_suspended).
    billing_service.assert_billing_not_suspended(db, account_id)

    # Fraud/Risk gates (Architecture doc §5 "Fraud and Risk", §13 "blocked
    # destinations; fraud thresholds") - checked before ever reaching Twilio.
    try:
        risk_service.assert_destination_allowed(db, to, account_id)
    except risk_service.DestinationBlockedError as e:
        notify_high_risk_destination_blocked(
            db, account_id=account_id, account_email=account_email,
            from_number=from_number, to_number=to, reason=str(e),
        )
        raise
    risk_service.assert_outbound_velocity_ok(db, account_id)
    risk_service.assert_concurrent_call_limit_ok(db, account_id)
    risk_service.assert_geographic_dispersion_ok(db, account_id, to)
    risk_service.assert_spend_limit_ok(db, account_id)
    # Production Readiness Standard Table 15 "Usage ceilings" - lifetime-
    # trial-spend hard limit, distinct from the rolling-window checks above
    # and from assert_concurrent_call_limit_ok's in-flight-call count.
    risk_service.assert_cumulative_trial_usage_ok(db, account_id)

    twiml = telecom.build_say_response(message)
    time_limit = risk_service.get_call_time_limit_for_account(db, account_id)
    result = telecom.place_call(
        to=to, from_=from_number, twiml=twiml, status_callback_url=status_callback_url,
        time_limit_seconds=time_limit,
    )

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


def place_outbound_call(
    db: Session, user: User, to: str, from_number: str, message: str, status_callback_url: str | None = None
) -> dict:
    owner = find_number_owner(db, from_number)
    if owner is None or owner.account_id != user.account_id or owner.status != PhoneNumberStatus.ACTIVE:
        raise CallAuthorizationError(f"{from_number} is not an active number owned by your account")
    try:
        assert_number_access(owner, user)
    except NumberConflictError as e:
        raise CallAuthorizationError(str(e)) from e

    return _dispatch_outbound_call(
        db, account_id=user.account_id, account_email=user.email, owner=owner, to=to, from_number=from_number,
        message=message, status_callback_url=status_callback_url,
    )


def place_outbound_call_for_account(
    db: Session, *, account_id: str, to: str, from_number: str, message: str,
    status_callback_url: str | None = None,
) -> dict:
    """Public API counterpart to place_outbound_call - an API key represents
    the whole account (see app.core.deps.get_api_key_account_id), equivalent
    to Owner/Admin access, so there's no per-Member number-assignment check
    to make here - there's no specific logged-in user to check it against."""
    owner = find_number_owner(db, from_number)
    if owner is None or owner.account_id != account_id or owner.status != PhoneNumberStatus.ACTIVE:
        raise CallAuthorizationError(f"{from_number} is not an active number owned by your account")

    from app.numbering.identity.models import User, UserRole

    account_owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    account_email = account_owner.email if account_owner else ""

    return _dispatch_outbound_call(
        db, account_id=account_id, account_email=account_email, owner=owner, to=to, from_number=from_number,
        message=message, status_callback_url=status_callback_url,
    )


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
    _invalidate_calls_cache(call.account_id)
    log_event(
        db, actor_id=call.account_id, action="call.status_updated",
        target_type="call_record", target_id=call.id, metadata={"status": status, "duration": duration},
    )
    if call.account_id and status in TERMINAL_CALL_STATUSES:
        publish_call_ended(call.account_id, call_sid=provider_call_sid, status=status, duration_seconds=duration)

    # Usage Metering (Roadmap §2 Voice scope; Architecture §7 "Usage Event"
    # data model) - every terminal call, not just completed ones, is a usage
    # event (Commercial Billing Operating Standard doc §E1: normalized usage
    # needs a disposition regardless of billability; §E2: attempted/busy/
    # no-answer/failed calls may carry real wholesale cost even though
    # they're not retail-billable). record_usage_event itself decides
    # billed quantity from `disposition` - this call site just reports what
    # actually happened.
    if status in TERMINAL_CALL_STATUSES and call.account_id is not None:
        country_band = None
        if call.phone_number_id is not None:
            number = db.query(PhoneNumber).filter(PhoneNumber.id == call.phone_number_id).first()
            country_band = number.country if number is not None else None
        usage_service.record_usage_event(
            db,
            account_id=call.account_id,
            event_type="call_seconds",
            quantity=duration or 0,
            unit="seconds",
            country_band=country_band,
            idempotency_key=f"call_seconds:{provider_call_sid}",
            disposition=status,
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
    _invalidate_calls_cache(call.account_id)
    log_event(
        db, actor_id=call.account_id, action="call.recorded",
        target_type="call_record", target_id=call.id, metadata={"duration": duration},
    )
    return call


def _calls_cache_key(account_id: str, limit: int) -> str:
    return f"calls:list:{account_id}:{limit}"


# Short TTL, invalidated on record_call/update_call_status/
# record_call_recording (see their call sites) plus the two staff/retention
# write paths (billing.service's wholesale-cost capture, retention.service's
# recording purge). Only ever caches the Owner/Admin (unfiltered) view -
# a Member's view additionally filters by assigned_number_ids BEFORE the
# limit is applied (see the docstring below), so serving it from an
# unfiltered cached page could silently return fewer/different rows than
# a live query would; Members fall through to a direct query every time
# instead, same as before this cache existed.
_CALLS_CACHE_TTL_SECONDS = 15


def _serialize_call(c: CallRecord) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "phone_number_id": c.phone_number_id,
        "direction": c.direction.value,
        "from_number": c.from_number,
        "to_number": c.to_number,
        "provider_call_sid": c.provider_call_sid,
        "status": c.status,
        "duration": c.duration,
        "recording_url": c.recording_url,
        "is_suspected_spam": c.is_suspected_spam,
        "wholesale_cost_cents": c.wholesale_cost_cents,
        "wholesale_currency": c.wholesale_currency,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _deserialize_call(data: dict) -> CallRecord:
    return CallRecord(
        id=data["id"],
        account_id=data["account_id"],
        phone_number_id=data["phone_number_id"],
        direction=CallDirection(data["direction"]),
        from_number=data["from_number"],
        to_number=data["to_number"],
        provider_call_sid=data["provider_call_sid"],
        status=data["status"],
        duration=data["duration"],
        recording_url=data["recording_url"],
        is_suspected_spam=data["is_suspected_spam"],
        wholesale_cost_cents=data["wholesale_cost_cents"],
        wholesale_currency=data["wholesale_currency"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_calls_cache(account_id: str | None) -> None:
    """Best-effort - only clears the default limit=20 entry (the dashboard's
    own request shape), same pragmatic scope as everywhere else in this
    pass. A staff/API caller using a non-default limit rides out the TTL
    instead of an exact invalidation - there's no registry of "every limit
    anyone has ever cached" to sweep here without adding real complexity
    for a rarely-used parameter."""
    if account_id:
        cache_delete(_calls_cache_key(account_id, 20))


def list_account_calls(db: Session, user: User, limit: int = 20) -> list[CallRecord]:
    """Owner/Admin see every call on the account. A plain Member only sees
    calls on numbers assigned to them - mirrors list_account_numbers."""
    ids = assigned_number_ids(db, user)
    if ids is not None:
        # Member view - filtering must happen BEFORE the limit is applied,
        # so this always queries live rather than risking a cached
        # Owner/Admin page that was limited/filtered in the wrong order.
        return (
            db.query(CallRecord)
            .filter(CallRecord.account_id == user.account_id, CallRecord.phone_number_id.in_(ids))
            .order_by(CallRecord.created_at.desc())
            .limit(limit)
            .all()
        )

    cache_key = _calls_cache_key(user.account_id, limit)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_call(row) for row in cached]
    calls = (
        db.query(CallRecord)
        .filter(CallRecord.account_id == user.account_id)
        .order_by(CallRecord.created_at.desc())
        .limit(limit)
        .all()
    )
    cache_set(cache_key, [_serialize_call(c) for c in calls], ttl_seconds=_CALLS_CACHE_TTL_SECONDS)
    return calls


def assert_can_access_call(db: Session, user: User, call_sid: str) -> None:
    """Security-review fix: GET /media/voice/calls/{call_sid} used to proxy
    straight to Twilio with no ownership check at all - any authenticated
    user on the platform could look up any other account's call metadata
    (phone numbers, duration, status) by SID. This enforces the same
    account/assignment boundary list_account_calls already uses, before the
    route is allowed to query the provider."""
    call = db.query(CallRecord).filter(CallRecord.provider_call_sid == call_sid).first()
    if call is None or call.account_id != user.account_id:
        raise CallAuthorizationError(f"{call_sid} is not a call owned by your account")
    ids = assigned_number_ids(db, user)
    if ids is not None and call.phone_number_id not in ids:
        raise CallAuthorizationError(f"{call_sid} is not a call owned by your account")


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
    _invalidate_voicemails_cache(account_id)
    log_event(
        db, actor_id=account_id, action="voicemail.created",
        target_type="voicemail", target_id=voicemail.id, metadata={"from": from_number},
    )
    publish_voicemail_created(account_id, voicemail_id=voicemail.id, phone_number_id=phone_number_id)

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        number = db.query(PhoneNumber).filter(PhoneNumber.id == phone_number_id).first()
        notify_voicemail_received(
            db, account_id=account_id, account_email=owner.email,
            e164=number.e164 if number else "", from_number=from_number, duration=duration,
        )

    return voicemail


def _voicemails_cache_key(account_id: str) -> str:
    return f"voicemails:list:{account_id}"


# Only ever caches the Owner/Admin (unfiltered) view, same reasoning as
# list_account_calls - a Member's phone_number_id filter must run before
# any row count/ordering decision a cache could otherwise get wrong.
_VOICEMAILS_CACHE_TTL_SECONDS = 15


def _serialize_voicemail(v: Voicemail) -> dict:
    return {
        "id": v.id,
        "phone_number_id": v.phone_number_id,
        "account_id": v.account_id,
        "from_number": v.from_number,
        "recording_url": v.recording_url,
        "duration": v.duration,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }


def _deserialize_voicemail(data: dict) -> Voicemail:
    return Voicemail(
        id=data["id"],
        phone_number_id=data["phone_number_id"],
        account_id=data["account_id"],
        from_number=data["from_number"],
        recording_url=data["recording_url"],
        duration=data["duration"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_voicemails_cache(account_id: str | None) -> None:
    if account_id:
        cache_delete(_voicemails_cache_key(account_id))


def list_account_voicemails(db: Session, user: User) -> list[Voicemail]:
    """Owner/Admin see every voicemail on the account. A plain Member only
    sees voicemails on numbers assigned to them."""
    ids = assigned_number_ids(db, user)
    if ids is not None:
        return (
            db.query(Voicemail)
            .filter(Voicemail.account_id == user.account_id, Voicemail.phone_number_id.in_(ids))
            .order_by(Voicemail.created_at.desc())
            .all()
        )

    cache_key = _voicemails_cache_key(user.account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_voicemail(row) for row in cached]
    voicemails = (
        db.query(Voicemail)
        .filter(Voicemail.account_id == user.account_id)
        .order_by(Voicemail.created_at.desc())
        .all()
    )
    cache_set(cache_key, [_serialize_voicemail(v) for v in voicemails], ttl_seconds=_VOICEMAILS_CACHE_TTL_SECONDS)
    return voicemails


def _find_account_video_session(db: Session, account_id: str, room_name: str) -> VideoSession:
    session = db.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    if session is None or session.account_id != account_id:
        raise VideoSessionAuthorizationError(f"{room_name} is not a video session owned by your account")
    return session


async def create_video_session(
    db: Session, account_id: str, host_user_id: str, confidential: bool = False
) -> VideoSession:
    # Graceful degradation (Architecture doc §9) - new video calls pause
    # once a payment grace period expires.
    billing_service.assert_billing_not_suspended(db, account_id)

    # Roadmap doc §8 "Phase 1... up to 8 participants" vs the "larger
    # meetings" Phase 3 tier - room capacity now follows the account's
    # actual plan instead of every account getting the same flat ceiling.
    subscription = billing_service.get_or_create_subscription(db, account_id)
    plan = billing_service.get_plan(db, subscription.plan_code)

    room_name = f"zl-{uuid.uuid4().hex[:16]}"
    await video.create_room(room_name, max_participants=plan.max_video_participants)

    session = VideoSession(
        account_id=account_id,
        host_user_id=host_user_id,
        room_name=room_name,
        status=VideoSessionStatus.ACTIVE,
        started_at=datetime.now(timezone.utc),
        confidential=confidential,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    log_event(
        db, actor_id=account_id, action="video.session.started",
        target_type="video_session", target_id=session.id,
        metadata={"room_name": room_name, "confidential": confidential},
    )
    publish_video_room_created(account_id, room_name=room_name)
    return session


async def end_video_session(db: Session, user: User, room_name: str) -> VideoSession:
    session = _find_account_video_session(db, user.account_id, room_name)
    if user.role == UserRole.MEMBER and session.host_user_id != user.id:
        raise VideoSessionAuthorizationError(f"{room_name} was not started by you")

    # Stop any in-progress recording first - ending the room doesn't
    # automatically stop egress, and a dangling egress job would keep
    # recording nothing useful (or error out) once the room is gone.
    # is_recording_in_progress, not a bare recording_egress_id check - a
    # manual stop_video_recording earlier in this same call already
    # stopped it, and re-stopping an already-finished egress would just
    # error against LiveKit for no reason.
    if is_recording_in_progress(session):
        await video.stop_room_recording(session.recording_egress_id)

    await video.end_room(room_name)

    session.status = VideoSessionStatus.ENDED
    session.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    log_event(
        db, actor_id=user.account_id, action="video.session.ended",
        target_type="video_session", target_id=session.id, metadata={"room_name": room_name},
    )
    publish_video_room_ended(user.account_id, room_name=room_name)
    return session


def is_recording_in_progress(session: VideoSession) -> bool:
    """True only while an egress is actively running - recording_egress_id
    stays set even after a finished recording's egress_ended webhook
    attaches recording_url (see _handle_egress_ended), since that same id
    is how that webhook finds this session in the first place. Using a
    bare `if session.recording_egress_id` check here would (incorrectly)
    treat "recorded earlier in this call" the same as "recording right
    now", permanently blocking a second recording in the same session."""
    return session.recording_egress_id is not None and session.recording_url is None


async def start_video_recording(db: Session, user: User, room_name: str) -> VideoSession:
    session = _find_account_video_session(db, user.account_id, room_name)
    if user.role == UserRole.MEMBER and session.host_user_id != user.id:
        raise VideoSessionAuthorizationError(f"{room_name} was not started by you")
    if session.status != VideoSessionStatus.ACTIVE:
        raise VideoSessionAuthorizationError(f"{room_name} is not an active session")
    if is_recording_in_progress(session):
        raise VideoSessionAuthorizationError(f"{room_name} is already being recorded")
    if session.confidential:
        raise ConfidentialModeRecordingBlockedError(
            f"{room_name} is a confidential session — recording is disabled and cannot be enabled"
        )
    if not has_active_consent(db, user.account_id, ConsentType.AI_PROCESSING):
        raise RecordingConsentRequiredError(
            "AI processing consent is required before recording video calls — "
            "grant it via POST /compliance/consent first"
        )

    egress_id = await video.start_room_recording(room_name)
    session.recording_egress_id = egress_id
    session.recording_url = None
    db.commit()
    db.refresh(session)
    log_event(
        db, actor_id=user.account_id, action="video.recording_started",
        target_type="video_session", target_id=session.id, metadata={"room_name": room_name},
    )
    return session


async def stop_video_recording(db: Session, user: User, room_name: str) -> VideoSession:
    """Manual stop, independent of ending the call - end_video_session
    already stops any in-progress recording on its own, so this is only
    for "keep talking, but stop recording" mid-call. recording_egress_id
    is deliberately left set (not cleared here) - the async egress_ended
    webhook still needs it to find this session and attach the finished
    recording_url; is_recording_in_progress is what actually gates whether a
    NEW recording can start, not this field's mere presence."""
    session = _find_account_video_session(db, user.account_id, room_name)
    if user.role == UserRole.MEMBER and session.host_user_id != user.id:
        raise VideoSessionAuthorizationError(f"{room_name} was not started by you")
    if not is_recording_in_progress(session):
        raise VideoSessionAuthorizationError(f"{room_name} is not currently being recorded")

    await video.stop_room_recording(session.recording_egress_id)
    log_event(
        db, actor_id=user.account_id, action="video.recording_stopped",
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


def _get_active_session_or_raise(db: Session, room_name: str) -> VideoSession:
    session = db.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    if session is None or session.status != VideoSessionStatus.ACTIVE:
        raise VideoSessionAuthorizationError(f"{room_name} is not an active video session")
    return session


# A guest waiting this long with no host response is treated as abandoned
# rather than left pending forever - see VideoWaitingGuest's docstring.
# Not a tuned production value, same "conservative first pass" caveat as
# every other threshold in this codebase.
WAITING_ROOM_TIMEOUT_MINUTES = 10


def _expire_if_stale(db: Session, waiting_guest: VideoWaitingGuest) -> VideoWaitingGuest:
    """Lazily flips a PENDING row to EXPIRED once it's outlived
    WAITING_ROOM_TIMEOUT_MINUTES - checked wherever a waiting_guest row is
    actually read (the guest's own poll, or the host's waiting-room list),
    so no separate cron/purge job is needed: every PENDING row already has
    someone polling it for as long as it matters."""
    if waiting_guest.status != VideoWaitingGuestStatus.PENDING:
        return waiting_guest
    age = datetime.now(timezone.utc) - waiting_guest.created_at
    if age > timedelta(minutes=WAITING_ROOM_TIMEOUT_MINUTES):
        waiting_guest.status = VideoWaitingGuestStatus.EXPIRED
        db.commit()
        db.refresh(waiting_guest)
        session = db.query(VideoSession).filter(VideoSession.id == waiting_guest.video_session_id).first()
        log_event(
            db, actor_id=session.account_id if session else None, action="video.guest_join_expired",
            target_type="video_session", target_id=waiting_guest.video_session_id,
            metadata={"waiting_id": waiting_guest.id, "display_name": waiting_guest.display_name},
        )
    return waiting_guest


def request_guest_join(db: Session, room_name: str, display_name: str) -> VideoWaitingGuest:
    """Public, unauthenticated request to join - no Zoiko account involved
    at all. The room_name itself (a random 64-bit-entropy `zl-<uuid hex>`
    string) is what gates access, the same trust model as sharing a Zoom/
    Meet invite link. Only an ACTIVE session can be joined, so an ended
    call's link can't be replayed later and a fabricated room name is
    rejected the same way a real-but-ended one is - this never reveals
    which case it was.

    Doesn't return a token - lands the guest in the waiting room instead
    (see check_waiting_status/admit_waiting_guest). The guest's identity is
    reserved now, server-generated (never client-supplied) so it can't
    collide with or impersonate a real user's identity, and prefixed
    `guest-` so participant records/audit are distinguishable from real
    account users at a glance.
    """
    session = _get_active_session_or_raise(db, room_name)

    guest_identity = f"guest-{uuid.uuid4().hex[:12]}"
    waiting_guest = VideoWaitingGuest(
        video_session_id=session.id, display_name=display_name, guest_identity=guest_identity,
    )
    db.add(waiting_guest)
    db.commit()
    db.refresh(waiting_guest)
    log_event(
        db, actor_id=session.account_id, action="video.guest_join_requested",
        target_type="video_session", target_id=session.id,
        metadata={"display_name": display_name, "guest_identity": guest_identity, "waiting_id": waiting_guest.id},
    )
    # Best-effort - the host might not be watching the call screen right
    # now, so this is the out-of-band alert that a guest is waiting (see
    # notify_video_guest_waiting's docstring). Never blocks the guest's own
    # request on an email-send failure.
    host = db.query(User).filter(User.id == session.host_user_id).first()
    if host is not None:
        notify_video_guest_waiting(
            db, account_id=session.account_id, host_email=host.email,
            room_name=room_name, guest_display_name=display_name,
        )
    return waiting_guest


def check_waiting_status(db: Session, room_name: str, waiting_id: str) -> dict:
    """Polled by the guest's browser while waiting for the host to respond.
    A LiveKit token is only ever generated here, on demand, once admitted -
    never persisted, since it's itself a bearer credential and there's no
    reason to store one at rest when it's this cheap to regenerate."""
    session = _get_active_session_or_raise(db, room_name)
    waiting_guest = (
        db.query(VideoWaitingGuest)
        .filter(VideoWaitingGuest.id == waiting_id, VideoWaitingGuest.video_session_id == session.id)
        .first()
    )
    if waiting_guest is None:
        raise WaitingGuestNotFoundError(f"{waiting_id} is not a waiting-room request for {room_name}")
    waiting_guest = _expire_if_stale(db, waiting_guest)

    if waiting_guest.status == VideoWaitingGuestStatus.ADMITTED:
        token = video.build_participant_token(room_name, waiting_guest.guest_identity, waiting_guest.display_name)
        # Disclosure only, not the guest's consent record - see the old
        # generate_guest_video_join_token's docstring this replaced; guests
        # have no account to attach a real consent record to.
        return {"status": "admitted", "token": token, "recording": session.recording_egress_id is not None}
    return {"status": waiting_guest.status.value, "token": None, "recording": False}


def _assert_can_manage_video_session(db: Session, user: User, room_name: str) -> VideoSession:
    session = _find_account_video_session(db, user.account_id, room_name)
    if user.role == UserRole.MEMBER and session.host_user_id != user.id:
        raise VideoSessionAuthorizationError(f"{room_name} was not started by you")
    return session


def list_waiting_guests(db: Session, user: User, room_name: str) -> list[VideoWaitingGuest]:
    session = _assert_can_manage_video_session(db, user, room_name)
    candidates = (
        db.query(VideoWaitingGuest)
        .filter(
            VideoWaitingGuest.video_session_id == session.id,
            VideoWaitingGuest.status == VideoWaitingGuestStatus.PENDING,
        )
        .order_by(VideoWaitingGuest.created_at.asc())
        .all()
    )
    # Expire stale ones before returning - a host opening the waiting-room
    # list shouldn't see (or be able to admit) a request the guest may have
    # long since given up on.
    still_pending = [_expire_if_stale(db, g) for g in candidates]
    return [g for g in still_pending if g.status == VideoWaitingGuestStatus.PENDING]


def _get_waiting_guest_for_host(db: Session, user: User, room_name: str, waiting_id: str) -> VideoWaitingGuest:
    session = _assert_can_manage_video_session(db, user, room_name)
    waiting_guest = (
        db.query(VideoWaitingGuest)
        .filter(VideoWaitingGuest.id == waiting_id, VideoWaitingGuest.video_session_id == session.id)
        .first()
    )
    if waiting_guest is None:
        raise WaitingGuestNotFoundError(f"{waiting_id} is not a waiting-room request for {room_name}")
    return _expire_if_stale(db, waiting_guest)


def admit_waiting_guest(db: Session, user: User, room_name: str, waiting_id: str) -> None:
    waiting_guest = _get_waiting_guest_for_host(db, user, room_name, waiting_id)
    if waiting_guest.status != VideoWaitingGuestStatus.PENDING:
        raise WaitingGuestNoLongerPendingError(
            f"{waiting_id} is already {waiting_guest.status.value} - nothing to admit"
        )
    waiting_guest.status = VideoWaitingGuestStatus.ADMITTED
    db.commit()
    log_event(
        db, actor_id=user.id, action="video.guest_admitted",
        target_type="video_session", target_id=waiting_guest.video_session_id,
        metadata={"waiting_id": waiting_id, "display_name": waiting_guest.display_name},
    )


def deny_waiting_guest(db: Session, user: User, room_name: str, waiting_id: str) -> None:
    waiting_guest = _get_waiting_guest_for_host(db, user, room_name, waiting_id)
    if waiting_guest.status != VideoWaitingGuestStatus.PENDING:
        raise WaitingGuestNoLongerPendingError(
            f"{waiting_id} is already {waiting_guest.status.value} - nothing to deny"
        )
    waiting_guest.status = VideoWaitingGuestStatus.DENIED
    db.commit()
    log_event(
        db, actor_id=user.id, action="video.guest_denied",
        target_type="video_session", target_id=waiting_guest.video_session_id,
        metadata={"waiting_id": waiting_id, "display_name": waiting_guest.display_name},
    )


def list_account_video_sessions(db: Session, account_id: str) -> list[VideoSession]:
    return (
        db.query(VideoSession)
        .filter(VideoSession.account_id == account_id)
        .order_by(VideoSession.created_at.desc())
        .all()
    )


def get_account_video_usage_minutes(db: Session, account_id: str) -> float:
    """Total participant-minutes across every video session this account
    has ever had - the account-level rollup the roadmap doc's usage-metering
    requirement is ultimately for (future ZoikoNex rating input)."""
    session_ids = [s.id for s in db.query(VideoSession.id).filter(VideoSession.account_id == account_id).all()]
    return round(sum(get_participant_minutes(db, sid) for sid in session_ids), 2)


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

    now = datetime.now(timezone.utc)

    if event.event == "room_finished":
        if session.status != VideoSessionStatus.ENDED:
            session.status = VideoSessionStatus.ENDED
            session.ended_at = now
            db.commit()
            log_event(
                db, actor_id=session.account_id, action="video.session.ended",
                target_type="video_session", target_id=session.id,
                metadata={"room_name": event.room.name, "source": "livekit_webhook"},
            )
        # Close out anyone whose participant_left event never arrived (e.g.
        # an abrupt disconnect) - otherwise their usage would count as
        # still-open/unbounded forever.
        dangling = (
            db.query(VideoParticipantSession)
            .filter(VideoParticipantSession.video_session_id == session.id, VideoParticipantSession.left_at.is_(None))
            .all()
        )
        for row in dangling:
            row.left_at = session.ended_at or now
        if dangling:
            db.commit()

        # Usage Metering (Architecture doc §7/§5) - room_finished is the one
        # point where every participant's time is finalized (dangling rows
        # just closed above), so it's the right moment to rate the whole
        # call's participant-minutes, not per-participant on each leave.
        usage_service.record_usage_event(
            db,
            account_id=session.account_id,
            event_type="video_participant_minutes",
            quantity=get_participant_minutes(db, session.id),
            unit="minutes",
            country_band=None,
            idempotency_key=f"video_participant_minutes:{session.id}",
        )
    elif event.event == "participant_joined":
        db.add(
            VideoParticipantSession(
                video_session_id=session.id, participant_identity=event.participant.identity, joined_at=now
            )
        )
        db.commit()
        log_event(
            db, actor_id=session.account_id, action="video.participant_joined",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": event.room.name, "participant_identity": event.participant.identity},
        )
    elif event.event == "participant_left":
        open_row = (
            db.query(VideoParticipantSession)
            .filter(
                VideoParticipantSession.video_session_id == session.id,
                VideoParticipantSession.participant_identity == event.participant.identity,
                VideoParticipantSession.left_at.is_(None),
            )
            .order_by(VideoParticipantSession.joined_at.desc())
            .first()
        )
        if open_row:
            open_row.left_at = now
            db.commit()
        log_event(
            db, actor_id=session.account_id, action="video.participant_left",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": event.room.name, "participant_identity": event.participant.identity},
        )


def get_recording_download_url(session: VideoSession) -> str | None:
    """The bucket is private (no public-read policy) - the permanent URL
    stored in recording_url 403s/UnauthorizedAccess's in a browser. Generate
    a fresh short-lived signed URL each time instead of ever serving that
    stored URL directly. Returns None if there's no recording, or it's
    already been purged by retention policy."""
    if not session.recording_url or session.recording_url == PURGED_MARKER:
        return None
    try:
        return generate_presigned_url(f"recordings/{session.room_name}.mp4")
    except StorageError:
        return None


def get_participant_minutes(db: Session, video_session_id: str) -> float:
    """Sum of every participant's time in the room - a 3-person, 10-minute
    call is 30 participant-minutes, not 10. Still-open rows (participant
    hasn't left / room hasn't ended) count up to now, so usage is visible
    even mid-call."""
    rows = (
        db.query(VideoParticipantSession)
        .filter(VideoParticipantSession.video_session_id == video_session_id)
        .all()
    )
    now = datetime.now(timezone.utc)
    total_duration = sum(((row.left_at or now) - row.joined_at for row in rows), timedelta())
    return round(total_duration.total_seconds() / 60, 2)


# Worse-than ranking, not alphabetical - used to decide whether a new sample
# should replace the stored "worst quality seen" for a participant.
_QUALITY_RANK = {ConnectionQuality.EXCELLENT: 0, ConnectionQuality.GOOD: 1, ConnectionQuality.POOR: 2}


class ParticipantSessionNotFoundError(Exception):
    """Raised when a quality sample arrives for a room/identity with no
    currently-open participant session - most likely a race between the
    client's first sample and LiveKit's participant_joined webhook, or a
    stale POST after the participant already left."""


def record_call_quality_sample(
    db: Session, room_name: str, participant_identity: str, *, quality: ConnectionQuality, reconnected: bool
) -> VideoParticipantSession:
    row = (
        db.query(VideoParticipantSession)
        .join(VideoSession, VideoSession.id == VideoParticipantSession.video_session_id)
        .filter(
            VideoSession.room_name == room_name,
            VideoParticipantSession.participant_identity == participant_identity,
            VideoParticipantSession.left_at.is_(None),
        )
        .order_by(VideoParticipantSession.joined_at.desc())
        .first()
    )
    if row is None:
        raise ParticipantSessionNotFoundError(
            f"No open participant session for {participant_identity!r} in {room_name!r}"
        )

    if row.worst_connection_quality is None or _QUALITY_RANK[quality] > _QUALITY_RANK[row.worst_connection_quality]:
        row.worst_connection_quality = quality
    if reconnected:
        row.reconnect_count += 1
    db.commit()
    db.refresh(row)
    return row


def get_call_quality_summary(db: Session, video_session_id: str) -> dict:
    """Aggregates every participant's telemetry for one call into a single
    room-level summary for the call-history list - the worst quality any
    participant experienced, and the total reconnects across all of them."""
    rows = (
        db.query(VideoParticipantSession)
        .filter(VideoParticipantSession.video_session_id == video_session_id)
        .all()
    )
    worst = None
    for row in rows:
        if row.worst_connection_quality is None:
            continue
        if worst is None or _QUALITY_RANK[row.worst_connection_quality] > _QUALITY_RANK[worst]:
            worst = row.worst_connection_quality
    return {
        "worst_connection_quality": worst.value if worst else None,
        "reconnect_count": sum(row.reconnect_count for row in rows),
    }


def _handle_egress_ended(db: Session, event) -> None:
    """Attaches the finished recording's file location once LiveKit's egress
    job completes - arrives asynchronously, well after the call itself ends."""
    egress_info = event.egress_info
    session = (
        db.query(VideoSession).filter(VideoSession.recording_egress_id == egress_info.egress_id).first()
    )
    if session is None:
        return

    # Some LiveKit server versions populate the repeated `file_results` list;
    # this deployment still only sets the deprecated singular `file` field
    # for a single-output RoomComposite egress - confirmed live, two
    # recordings completed and uploaded fine but never got a recording_url
    # because only `file_results` was checked. Check both, newer field first.
    location = egress_info.file_results[0].location if egress_info.file_results else egress_info.file.location

    if location:
        session.recording_url = location
        db.commit()
        log_event(
            db, actor_id=session.account_id, action="video.recording_completed",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": session.room_name, "egress_status": egress_info.status},
        )
    else:
        log_event(
            db, actor_id=session.account_id, action="video.recording_failed",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": session.room_name, "egress_status": egress_info.status, "error": egress_info.error},
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
        qualification, model_version = qualify_caller(db, owner.account_id, transcript, owner.country)
    except LLMError:
        pass

    qualification = qualification or {}
    urgency_raw = qualification.get("urgency")
    urgency = ReceptionistUrgency(urgency_raw) if urgency_raw in ("low", "medium", "high") else None
    # Guardrail check on the AI's OWN generated text, not the caller's raw
    # transcript - see app/intelligence/guardrails.py.
    guardrail_flags = check_for_disallowed_commitments(qualification.get("summary"), qualification.get("reason"))
    is_likely_spam = bool(qualification.get("is_likely_spam"))
    spam_reason = qualification.get("spam_reason") if is_likely_spam else None

    call = ReceptionistCall(
        account_id=owner.account_id,
        phone_number_id=owner.id,
        call_sid=call_sid,
        caller_number=from_number,
        raw_transcript=transcript,
        caller_name=qualification.get("name"),
        caller_company=qualification.get("company"),
        reason=qualification.get("reason"),
        summary=qualification.get("summary"),
        urgency=urgency,
        guardrail_flags=guardrail_flags,
        is_likely_spam=is_likely_spam,
        spam_reason=spam_reason,
        model_version=model_version,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    log_event(
        db, actor_id=owner.account_id, action="receptionist.call_captured",
        target_type="receptionist_call", target_id=call.id,
        metadata={"urgency": urgency.value if urgency else None, "guardrail_flags": guardrail_flags, "is_likely_spam": is_likely_spam},
    )
    return call


def mark_receptionist_call_escalated(db: Session, receptionist_call_id: str, escalated_to_user_id: str) -> None:
    call = db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()
    if call is None:
        return
    call.escalated = True
    db.commit()
    # Roadmap §7 Evidence: "escalation path" - who this specific urgent call
    # was routed to, not just that some escalation happened.
    log_event(
        db, actor_id=call.account_id, action="receptionist.call_escalated",
        target_type="receptionist_call", target_id=call.id,
        metadata={"escalated_to_user_id": escalated_to_user_id},
    )


def route_receptionist_call(
    db: Session, user: User, receptionist_call_id: str, assigned_user_id: str | None
) -> ReceptionistCall:
    """The human-decision counterpart to mark_receptionist_call_escalated -
    routes an already-captured call's summary to a team member for
    follow-up (Roadmap §7 / marketing site's "Approve & route" flow), not a
    live-call action. Passing assigned_user_id=None un-assigns it."""
    call = db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()
    if call is None or call.account_id != user.account_id:
        raise ReceptionistCallAuthorizationError(
            f"{receptionist_call_id} is not a receptionist call owned by your account"
        )
    if call.phone_number_id is not None:
        number = db.query(PhoneNumber).filter(PhoneNumber.id == call.phone_number_id).first()
        if number is not None:
            try:
                assert_number_access(number, user)
            except NumberConflictError as e:
                raise ReceptionistCallAuthorizationError(str(e)) from e

    if assigned_user_id is not None:
        nominee = db.query(User).filter(User.id == assigned_user_id, User.account_id == user.account_id).first()
        if nominee is None:
            raise ReceptionistCallAuthorizationError(f"No team member with id {assigned_user_id} on this account")

    call.assigned_user_id = assigned_user_id
    db.commit()
    db.refresh(call)
    log_event(
        db, actor_id=user.id, action="receptionist.call_routed",
        target_type="receptionist_call", target_id=call.id,
        metadata={"assigned_user_id": assigned_user_id},
    )
    return call


def edit_receptionist_call_summary(
    db: Session, user: User, receptionist_call_id: str, new_summary: str
) -> ReceptionistCall:
    """AI governance's "human-editable outputs" requirement, applied to the
    receptionist's narrated summary - same access boundary as routing it
    (assert_number_access), and never re-runs the guardrail check: a human
    correcting the wording is a deliberate decision, not unvetted model
    output."""
    call = db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()
    if call is None or call.account_id != user.account_id:
        raise ReceptionistCallAuthorizationError(
            f"{receptionist_call_id} is not a receptionist call owned by your account"
        )
    if call.phone_number_id is not None:
        number = db.query(PhoneNumber).filter(PhoneNumber.id == call.phone_number_id).first()
        if number is not None:
            try:
                assert_number_access(number, user)
            except NumberConflictError as e:
                raise ReceptionistCallAuthorizationError(str(e)) from e

    if call.original_summary is None:
        call.original_summary = call.summary
    call.summary = new_summary
    call.edited_at = datetime.now(timezone.utc)
    call.edited_by_user_id = user.id
    db.commit()
    db.refresh(call)
    log_event(
        db, actor_id=user.id, action="receptionist.call_summary_edited",
        target_type="receptionist_call", target_id=call.id, metadata={"edited": True},
    )
    return call


def list_account_receptionist_calls(db: Session, user: User) -> list[ReceptionistCall]:
    """Owner/Admin see every receptionist call on the account. A plain Member
    only sees calls on numbers assigned to them."""
    query = db.query(ReceptionistCall).filter(ReceptionistCall.account_id == user.account_id)
    ids = assigned_number_ids(db, user)
    if ids is not None:
        query = query.filter(ReceptionistCall.phone_number_id.in_(ids))
    return query.order_by(ReceptionistCall.created_at.desc()).all()
