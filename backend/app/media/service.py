import asyncio
import math
import re
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
    publish_video_session_ended,
    publish_video_session_started,
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
    notify_missed_call,
    notify_receptionist_callback_requested,
    notify_video_guest_waiting,
    notify_voicemail_received,
)
from app.media.models import (
    TERMINAL_CALL_STATUSES,
    CallDirection,
    CallRecord,
    ConnectionQuality,
    ReceptionistCall,
    ReceptionistCallbackWindow,
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
from app.numbering.numbers.service import (
    CallerIdNotAuthorizedError,
    NumberConflictError,
    assert_caller_id_authorized,
    assert_number_access,
    assigned_number_ids,
)
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active
from app.retention.service import ERASED_MARKER, PURGED_MARKER, RECORDING_FAILED_MARKER
from app.risk import service as risk_service
from app.usage import service as usage_service

# Generous upper bound on any real meeting length + LiveKit's own upload/
# processing time - only exists to catch a genuinely lost webhook (LiveKit
# outage, dropped delivery), not to time out recordings that are still
# legitimately in progress.
RECORDING_STALE_AFTER_MINUTES = 120


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


def should_forward_call(owner: PhoneNumber, *, has_ring_group: bool = False) -> bool:
    """has_ring_group (real gap fix): a ring group used to be invisible
    here entirely - only forwarding_number's presence ever flipped this to
    True, so an account that configured a ring group but never separately
    set the legacy forwarding_number field got zero forwarding at all
    (every call fell straight through to AI receptionist/voicemail). The
    caller (media.voice._default_call_twiml) computes has_ring_group from
    the same numbers_service.list_ring_group query it already needs for
    the actual destinations list, so this adds no extra query here."""
    if not owner.forwarding_number and not has_ring_group:
        return False
    if owner.business_hours_start is None or owner.business_hours_end is None:
        return True  # forwarding configured with no schedule restriction = always forward
    return is_within_business_hours(owner.business_hours_start, owner.business_hours_end, owner.business_hours_timezone)


def should_record_forwarded_call(db: Session, account_id: str) -> bool:
    """Architecture doc §2.2: "Recording: off by default. Where enabled, it
    must be consented..." - reuses the same AI_PROCESSING consent record the
    video-recording feature gates on, rather than recording every forwarded
    call unconditionally the moment forwarding_number is configured. Also
    checks the ZL-COM-ENT-001 v3.0 recording.policy_enabled plan gate -
    additive to consent, not a replacement. This feeds TwiML for a live
    Twilio webhook, so it fails closed/silent (has_entitlement) rather than
    raising (assert_entitlement would 500 the call)."""
    return has_active_consent(db, account_id, ConsentType.AI_PROCESSING) and billing_service.has_entitlement(
        db, account_id, "recording.policy_enabled"
    )


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
        actor_id=account_id if account_id else "system:unrecognized_number",
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


def assert_outbound_call_allowed(
    db: Session, *, account_id: str, account_email: str, to: str, from_number: str,
) -> None:
    """Shared risk/billing preamble for every real outbound call this
    account places, however the call is ultimately built (a one-way
    announcement in _dispatch_outbound_call, a live call-bridge in
    _dispatch_bridge_call, or an agent-pull in queues.service.
    pull_next_caller) - the destination doesn't know or care which kind of
    call it is, so it must clear the same gates either way. Not module-
    private (no leading underscore) precisely because queues.service needs
    to call this too - pull_next_caller previously placed a real outbound
    call via telecom.place_call with none of these checks at all, a real
    kill-switch/billing-suspension/toll-fraud bypass fixed by reusing this
    instead of duplicating (and inevitably drifting from) it."""
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


def _dispatch_outbound_call(
    db: Session, *, account_id: str, account_email: str, owner: PhoneNumber, to: str, from_number: str,
    message: str, status_callback_url: str | None,
) -> dict:
    """Shared core of place_outbound_call and place_outbound_call_for_account
    - everything after "is this number really available to the caller" is
    identical for both a logged-in user and a public API key."""
    assert_outbound_call_allowed(db, account_id=account_id, account_email=account_email, to=to, from_number=from_number)

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


def place_bridge_call(
    db: Session, user: User, from_number: str, to: str, agent_number: str,
    bridge_connect_url: str, status_callback_url: str | None,
) -> dict:
    """Live two-way calling ("click to call"): rings agent_number first -
    any real phone the caller supplies for this specific call, not a saved
    setting - and only once THEY pick up does Twilio request
    bridge_connect_url, which dials the real customer and joins the two
    legs. The agent experiences this as an ordinary phone call; the
    platform is just the operator connecting two real calls, the same
    primitive ring groups already use, aimed the other direction. Unlike
    place_outbound_call's one-way announcement, both sides get genuine
    live audio."""
    owner = find_number_owner(db, from_number)
    if owner is None or owner.account_id != user.account_id or owner.status != PhoneNumberStatus.ACTIVE:
        raise CallAuthorizationError(f"{from_number} is not an active number owned by your account")
    try:
        assert_number_access(owner, user)
    except NumberConflictError as e:
        raise CallAuthorizationError(str(e)) from e
    try:
        assert_caller_id_authorized(db, owner.id)
    except CallerIdNotAuthorizedError as e:
        raise CallAuthorizationError(str(e)) from e

    assert_outbound_call_allowed(
        db, account_id=user.account_id, account_email=user.email, to=to, from_number=from_number,
    )
    # agent_number is a real destination Twilio dials immediately below - a
    # free-form per-call number, not a saved/vetted forwarding_number - so
    # it needs the same destination-specific fraud/compliance checks as the
    # customer `to` above. The account-wide gates (kill switch, billing,
    # velocity, spend) were already covered by assert_outbound_call_allowed.
    try:
        risk_service.assert_destination_allowed(db, agent_number, user.account_id)
    except risk_service.DestinationBlockedError as e:
        notify_high_risk_destination_blocked(
            db, account_id=user.account_id, account_email=user.email,
            from_number=from_number, to_number=agent_number, reason=str(e),
        )
        raise
    risk_service.assert_geographic_dispersion_ok(db, user.account_id, agent_number)

    time_limit = risk_service.get_call_time_limit_for_account(db, user.account_id)
    result = telecom.place_call(
        to=agent_number, from_=from_number, twiml_url=bridge_connect_url,
        status_callback_url=status_callback_url, time_limit_seconds=time_limit,
    )

    record_call(
        db,
        account_id=user.account_id,
        phone_number_id=owner.id,
        direction=CallDirection.OUTBOUND,
        from_number=from_number,
        to_number=to,
        provider_call_sid=result["sid"],
        status=result["status"],
    )
    return result


def handle_browser_connect(
    db: Session, *, account_id: str, from_number: str, to: str, call_sid: str, status_callback_url: str | None = None,
) -> dict:
    """Live two-way calling straight from the browser (@twilio/voice-sdk):
    called by media.voice.browser_connect, the webhook Twilio hits the
    instant someone's browser (already holding a token minted for
    `account_id` - see telecom.build_voice_access_token) places a call via
    Device.connect(). Unlike place_bridge_call, there's no agent phone to
    ring first - the browser itself IS the agent leg by the time this
    runs, so this only has to authorize `from_number` for `account_id`.
    Raises the same CallAuthorizationError/risk exceptions as every other
    real call so the route can translate them into a clean spoken error
    instead of dead air.

    Returns a dict describing HOW to route the call rather than building
    TwiML directly (unlike every other place_* call here) - TwiML
    construction needs `request`/routing_service, which live in
    media.voice, not here. Two shapes:
      - {"mode": "pstn_bridge", "destination": to, "caller_id": from_number}
        - the normal case, dial `to` over the carrier network.
      - {"mode": "app_to_app", "owner": <PhoneNumber>} - ZL-COM-ENT-001
        v3.0 voice.app_to_app: `to` is owned by a DIFFERENT Zoiko account
        and the caller's plan includes app-to-app calling. Routed through
        that account's OWN real call handling (ring group/business hours/
        AI receptionist/voicemail via media.voice._resolve_call_twiml),
        not a bare client-to-client bridge that would skip it entirely -
        skips the PSTN leg, not the callee's configured call handling.
        has_entitlement (not assert_entitlement) - voice.app_to_app is
        Included on every real plan, so lacking it just falls back to
        the normal pstn_bridge mode (dialing `to` as an ordinary number
        still works fine), never a hard denial for what's meant to be a
        transparent optimization."""
    # Real gap found live: Twilio hit this webhook at least twice with To
    # blank (both from the account's own browser client, no destination at
    # all) - assert_outbound_call_allowed doesn't validate this, so it fell
    # through to build_bridge_response with an empty <Number>, dialing
    # nothing. Not clear yet whether this is a stray client-side call
    # attempt or a genuine Twilio-side quirk; either way this is the
    # correct, defensive response until the real trigger is understood.
    if not to:
        raise CallAuthorizationError("No destination number was provided")

    owner = find_number_owner(db, from_number)
    if owner is None or owner.account_id != account_id or owner.status != PhoneNumberStatus.ACTIVE:
        raise CallAuthorizationError(f"{from_number} is not an active number owned by your account")
    try:
        assert_caller_id_authorized(db, owner.id)
    except CallerIdNotAuthorizedError as e:
        raise CallAuthorizationError(str(e)) from e

    account_owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    account_email = account_owner.email if account_owner else ""

    assert_outbound_call_allowed(db, account_id=account_id, account_email=account_email, to=to, from_number=from_number)

    record_call(
        db,
        account_id=account_id,
        phone_number_id=owner.id,
        direction=CallDirection.OUTBOUND,
        from_number=from_number,
        to_number=to,
        provider_call_sid=call_sid,
        status="in-progress",
    )

    target_owner = find_number_owner(db, to)
    if (
        target_owner is not None
        and target_owner.account_id != account_id
        and target_owner.status == PhoneNumberStatus.ACTIVE
        and billing_service.has_entitlement(db, account_id, "voice.app_to_app")
    ):
        return {"mode": "app_to_app", "owner": target_owner}
    return {"mode": "pstn_bridge", "destination": to, "caller_id": from_number}


class CallNotTransferableError(Exception):
    """Raised by transfer_call when the target call doesn't exist, isn't
    owned by the caller's account, or isn't currently in-progress."""


def transfer_call(db: Session, user: User, call_sid: str, destination: str) -> dict:
    """ZL-COM-ENT-001 v3.0 - routing.transfer (Business+). Blind/cold
    transfer only: redirects the call's live leg to fresh TwiML dialing
    `destination`, dropping whoever was on the transferring leg - not a
    3-way warm/attended transfer (a materially bigger feature, needing a
    conference bridge). Reuses assert_can_access_call's exact ownership
    check (account + assigned-number scoping) rather than a new one."""
    assert_can_access_call(db, user, call_sid)
    billing_service.assert_entitlement(db, user.account_id, "routing.transfer")

    call = db.query(CallRecord).filter(CallRecord.provider_call_sid == call_sid).first()
    if call is None or call.status != "in-progress":
        raise CallNotTransferableError(f"{call_sid} is not an in-progress call")
    if not destination or not destination.startswith("+"):
        raise CallNotTransferableError("destination must be a real phone number in E.164 format")

    twiml = telecom.build_bridge_response(destination, caller_id=call.from_number)
    result = telecom.redirect_call(call_sid, twiml)
    log_event(
        db, actor_id=user.id, action="call.transferred", target_type="call_record", target_id=call.id,
        metadata={"call_sid": call_sid, "destination": destination},
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
    try:
        assert_caller_id_authorized(db, owner.id)
    except CallerIdNotAuthorizedError as e:
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
    try:
        assert_caller_id_authorized(db, owner.id)
    except CallerIdNotAuthorizedError as e:
        raise CallAuthorizationError(str(e)) from e

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
        # AI Receptionist minute metering (Pricing doc §5.3) - only for
        # calls the receptionist actually handled. Whole-call duration, same
        # caveat as call_seconds above: every TwiML leg, not narrowly
        # "AI-processing time". This meters raw usage only; it does not
        # enforce/bill the doc's included-allowance + overage rule - see
        # billing.get_usage_summary, which reports this the same
        # informational-only way it reports every other metered resource.
        receptionist_call = (
            db.query(ReceptionistCall).filter(ReceptionistCall.call_sid == provider_call_sid).first()
        )
        if receptionist_call is not None:
            receptionist_call.duration_seconds = duration or 0
            db.commit()
            usage_service.record_usage_event(
                db,
                account_id=call.account_id,
                event_type="ai_receptionist_minutes",
                quantity=math.ceil((duration or 0) / 60),
                unit="minutes",
                country_band=country_band,
                idempotency_key=f"ai_receptionist_minutes:{provider_call_sid}",
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


def public_recording_url(recording_url: str | None) -> str | None:
    """A purged/erased recording's column value is one of the internal
    marker strings (PURGED_MARKER/ERASED_MARKER/RECORDING_FAILED_MARKER),
    not a real URL - those must never reach the frontend as if they were
    a playable recording_url, or Play/Summarize would show up for audio
    that no longer exists. media.service.get_call_recording_media already
    guards this on the fetch path (raises TelecomError for a marker); this
    is the matching guard for the list/read side. Same check
    should_forward_call/video's own inline version at line ~1417 already
    does for video sessions - factored out here so call/voicemail listings
    use the identical rule instead of a second, driftable copy."""
    if not recording_url or recording_url in (PURGED_MARKER, ERASED_MARKER, RECORDING_FAILED_MARKER):
        return None
    return recording_url


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


def get_call_recording_media(db: Session, user: User, call_sid: str) -> tuple[bytes, str]:
    """Twilio recording URLs need Twilio's own Basic Auth credentials to
    fetch (see twilio.get_recording_media's docstring) - a browser opening
    the stored recording_url directly gets a login prompt instead of
    audio. This does the same ownership check as assert_can_access_call,
    then fetches the audio server-side so the frontend never needs to
    know Twilio credentials exist."""
    assert_can_access_call(db, user, call_sid)
    call = db.query(CallRecord).filter(CallRecord.provider_call_sid == call_sid).first()
    if call is None or not call.recording_url:
        raise CallAuthorizationError(f"{call_sid} has no recording")
    return telecom.get_recording_media(call.recording_url)


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


def get_voicemail_recording_media(db: Session, user: User, voicemail_id: str) -> tuple[bytes, str]:
    """Same rationale as get_call_recording_media - voicemail.recording_url
    is also a Twilio-authenticated URL a browser can't fetch directly."""
    voicemail = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
    if voicemail is None or voicemail.account_id != user.account_id:
        raise CallAuthorizationError(f"{voicemail_id} is not a voicemail owned by your account")
    ids = assigned_number_ids(db, user)
    if ids is not None and voicemail.phone_number_id not in ids:
        raise CallAuthorizationError(f"{voicemail_id} is not a voicemail owned by your account")
    if not voicemail.recording_url:
        raise CallAuthorizationError(f"{voicemail_id} has no recording")
    return telecom.get_recording_media(voicemail.recording_url)


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
    _invalidate_video_sessions_cache(account_id)
    log_event(
        db, actor_id=account_id, action="video.session.started",
        target_type="video_session", target_id=session.id,
        metadata={"room_name": room_name, "confidential": confidential},
    )
    publish_video_room_created(account_id, room_name=room_name)
    publish_video_session_started(account_id, session_id=session.id, room_name=room_name)
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
    _invalidate_video_sessions_cache(user.account_id)
    log_event(
        db, actor_id=user.account_id, action="video.session.ended",
        target_type="video_session", target_id=session.id, metadata={"room_name": room_name},
    )
    publish_video_room_ended(user.account_id, room_name=room_name)
    publish_video_session_ended(user.account_id, session_id=session.id, room_name=room_name)
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


def _build_recording_object_key(db: Session, session: VideoSession, started_at: datetime) -> str:
    """Human-readable S3 key - account name + date/day/time - instead of
    the internal random room_name ("zl-<uuid hex>"), which is fine as a
    LiveKit/DB identifier but meaningless to a human looking at recording
    filenames in the bucket. A short suffix off the session id is appended
    to guarantee uniqueness even if two recordings from the same account
    started in the same second (timestamp resolution is whole seconds)."""
    from app.numbering.identity.models import Account

    account = db.query(Account).filter(Account.id == session.account_id).first()
    raw_name = account.name if account is not None else "account"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw_name).strip("-").lower() or "account"
    timestamp = started_at.strftime("%Y-%m-%d-%A-%H-%M-%S")
    suffix = session.id.replace("-", "")[:8]
    return f"recordings/{slug}-{timestamp}-{suffix}.mp4"


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
    # ZL-COM-ENT-001 v3.0 - plan-tier gate, additive to (not a replacement
    # for) the AI_PROCESSING consent check above: consent covers "may we
    # legally process this," the entitlement covers "does this plan
    # include recording at all."
    billing_service.assert_entitlement(db, user.account_id, "recording.policy_enabled")

    started_at = datetime.now(timezone.utc)
    object_key = _build_recording_object_key(db, session, started_at)
    egress_id = await video.start_room_recording(room_name, object_key)
    session.recording_egress_id = egress_id
    session.recording_url = None
    session.recording_object_key = object_key
    session.recording_started_at = started_at
    db.commit()
    db.refresh(session)
    _invalidate_video_sessions_cache(user.account_id)
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
    _invalidate_waiting_guests_cache(session.id)
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


def _waiting_guests_cache_key(video_session_id: str) -> str:
    return f"waiting_guests:list:{video_session_id}"


# Short TTL, same "don't chase every write site" posture as notifications.
# service's list cache: the host's browser polls this every 3 seconds for
# the whole duration of a call (see the video page's waiting-room poll), so
# this is actually a higher-frequency read than anything else cached in this
# codebase. Still invalidated at request_guest_join/admit_waiting_guest/
# deny_waiting_guest below - unlike notifications' ~40 send call sites,
# there are only 3 write sites here, cheap to cover directly - but NOT
# re-checked against _expire_if_stale's lazy, read-triggered time-based
# expiry, which a cache can't preemptively invalidate against. A guest who
# timed out may still show as pending for up to this TTL; admit/deny both
# re-check staleness live via _get_waiting_guest_for_host regardless of
# what this cached list showed, so that bounded staleness window is
# cosmetic only, never a correctness issue for the actual admit/deny action.
_WAITING_GUESTS_CACHE_TTL_SECONDS = 5


def _serialize_waiting_guest(g: VideoWaitingGuest) -> dict:
    return {
        "id": g.id,
        "video_session_id": g.video_session_id,
        "display_name": g.display_name,
        "guest_identity": g.guest_identity,
        "status": g.status.value,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


def _deserialize_waiting_guest(data: dict) -> VideoWaitingGuest:
    return VideoWaitingGuest(
        id=data["id"],
        video_session_id=data["video_session_id"],
        display_name=data["display_name"],
        guest_identity=data["guest_identity"],
        status=VideoWaitingGuestStatus(data["status"]),
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_waiting_guests_cache(video_session_id: str) -> None:
    cache_delete(_waiting_guests_cache_key(video_session_id))


def list_waiting_guests(db: Session, user: User, room_name: str) -> list[VideoWaitingGuest]:
    session = _assert_can_manage_video_session(db, user, room_name)
    cache_key = _waiting_guests_cache_key(session.id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_waiting_guest(row) for row in cached]

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
    result = [g for g in still_pending if g.status == VideoWaitingGuestStatus.PENDING]
    cache_set(cache_key, [_serialize_waiting_guest(g) for g in result], ttl_seconds=_WAITING_GUESTS_CACHE_TTL_SECONDS)
    return result


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
    _invalidate_waiting_guests_cache(waiting_guest.video_session_id)
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
    _invalidate_waiting_guests_cache(waiting_guest.video_session_id)
    log_event(
        db, actor_id=user.id, action="video.guest_denied",
        target_type="video_session", target_id=waiting_guest.video_session_id,
        metadata={"waiting_id": waiting_id, "display_name": waiting_guest.display_name},
    )


def _video_sessions_cache_key(account_id: str) -> str:
    return f"video_sessions:list:{account_id}"


# Same short-TTL, invalidate-on-write pattern as _CALLS_CACHE_TTL_SECONDS/
# _VOICEMAILS_CACHE_TTL_SECONDS above - the video Call History page (GET
# /media/video/rooms) refetches this list after every join/end/record/
# summarize action, same "highest-traffic dashboard endpoint" justification.
# Invalidated at every VideoSession field mutation that list_account_video_
# sessions' serialized output actually reflects: create_video_session,
# end_video_session, start_video_recording, handle_video_webhook_event's
# room_finished transition, _handle_egress_ended (attaches recording_url,
# which is what makes "Play recording"/"Summarize with AI" appear), and
# sweep_stale_video_recordings (clears a stuck in-progress state when the
# egress_ended webhook never arrives at all).
_VIDEO_SESSIONS_CACHE_TTL_SECONDS = 15


def _serialize_video_session(s: VideoSession) -> dict:
    return {
        "id": s.id,
        "account_id": s.account_id,
        "host_user_id": s.host_user_id,
        "room_name": s.room_name,
        "status": s.status.value,
        "started_at": s.started_at.isoformat() if s.started_at else None,
        "ended_at": s.ended_at.isoformat() if s.ended_at else None,
        "confidential": s.confidential,
        "recording_egress_id": s.recording_egress_id,
        "recording_url": s.recording_url,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _deserialize_video_session(data: dict) -> VideoSession:
    return VideoSession(
        id=data["id"],
        account_id=data["account_id"],
        host_user_id=data["host_user_id"],
        room_name=data["room_name"],
        status=VideoSessionStatus(data["status"]),
        started_at=datetime.fromisoformat(data["started_at"]) if data["started_at"] else None,
        ended_at=datetime.fromisoformat(data["ended_at"]) if data["ended_at"] else None,
        confidential=data["confidential"],
        recording_egress_id=data["recording_egress_id"],
        recording_url=data["recording_url"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_video_sessions_cache(account_id: str | None) -> None:
    if account_id:
        cache_delete(_video_sessions_cache_key(account_id))


def list_account_video_sessions(db: Session, account_id: str) -> list[VideoSession]:
    cache_key = _video_sessions_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_video_session(row) for row in cached]
    sessions = (
        db.query(VideoSession)
        .filter(VideoSession.account_id == account_id)
        .order_by(VideoSession.created_at.desc())
        .all()
    )
    cache_set(cache_key, [_serialize_video_session(s) for s in sessions], ttl_seconds=_VIDEO_SESSIONS_CACHE_TTL_SECONDS)
    return sessions


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
            _invalidate_video_sessions_cache(session.account_id)
            log_event(
                db, actor_id=session.account_id, action="video.session.ended",
                target_type="video_session", target_id=session.id,
                metadata={"room_name": event.room.name, "source": "livekit_webhook"},
            )
            # end_video_session (the explicit POST /rooms/{name}/end path)
            # publishes these same two events - this webhook path is how a
            # room ends when participants just leave without anyone
            # explicitly ending the call (the more common case in practice),
            # and was missing them entirely until now, so any real consumer
            # of zoiko.video's video.room.ended/video.session.ended silently
            # missed most actual call endings.
            publish_video_room_ended(session.account_id, room_name=event.room.name)
            publish_video_session_ended(session.account_id, session_id=session.id, room_name=event.room.name)
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
    already been purged by retention policy, or the recording failed.

    Uses recording_object_key (the actual key it was uploaded under) rather
    than reconstructing one from room_name - sessions recorded before
    recording_object_key existed fall back to the old room_name-based
    scheme, since that's genuinely what those older files are keyed by."""
    if not session.recording_url or session.recording_url in (PURGED_MARKER, RECORDING_FAILED_MARKER):
        return None
    key = session.recording_object_key or f"recordings/{session.room_name}.mp4"
    try:
        return generate_presigned_url(key)
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
        _invalidate_video_sessions_cache(session.account_id)
        log_event(
            db, actor_id=session.account_id, action="video.recording_completed",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": session.room_name, "egress_status": egress_info.status},
        )
    else:
        # Previously left recording_egress_id set with recording_url still
        # None - is_recording_in_progress stayed True forever (a real,
        # confirmed bug: blocked starting a new recording in the same call
        # with a wrong "already being recorded" error, and the frontend
        # showed "Recording processing..." with no way to tell the customer
        # anything actually went wrong). Clearing egress_id and setting the
        # marker resolves both at once.
        session.recording_egress_id = None
        session.recording_url = RECORDING_FAILED_MARKER
        db.commit()
        _invalidate_video_sessions_cache(session.account_id)
        log_event(
            db, actor_id=session.account_id, action="video.recording_failed",
            target_type="video_session", target_id=session.id,
            metadata={"room_name": session.room_name, "egress_status": egress_info.status, "error": egress_info.error},
        )


def sweep_stale_video_recordings(db: Session) -> dict[str, int]:
    """Catches the other half of the stuck-recording bug _handle_egress_ended
    fixes: a genuinely LOST egress_ended webhook (LiveKit outage, our own
    backend restarting mid-delivery, dropped delivery) never calls that
    function at all, so nothing would otherwise ever clear
    recording_egress_id. Same daily-scheduled-sweep pattern as
    compliance.service.expire_overdue_cases/retention.service.
    purge_expired_recordings - meant to run periodically, not on every
    request.

    Confirmed live (2026-08-21): a lost webhook does NOT mean the recording
    failed - two real recordings completed successfully on LiveKit's side
    (real files uploaded) purely because our own backend happened to be
    mid-restart the moment the webhook tried to deliver. Blindly marking
    every stale row RECORDING_FAILED_MARKER on elapsed time alone would
    have permanently hidden those real recordings from the customer (the
    marker overwrites recording_url, nothing ever reconciles it back).
    Actively asks LiveKit for the true status via get_egress_status first -
    only recovers/fails based on what LiveKit actually reports, and leaves
    a row untouched (retried on the next sweep) if that check itself fails,
    rather than guessing."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=RECORDING_STALE_AFTER_MINUTES)
    stale = (
        db.query(VideoSession)
        .filter(
            VideoSession.recording_egress_id.isnot(None),
            VideoSession.recording_url.is_(None),
            VideoSession.recording_started_at.isnot(None),
            VideoSession.recording_started_at < cutoff,
        )
        .all()
    )
    swept = 0
    for session in stale:
        try:
            info = asyncio.run(video.get_egress_status(session.recording_egress_id))
        except video.VideoError:
            continue  # LiveKit itself unreachable right now - retry next sweep, don't guess

        if info is not None and info["status"] in (0, 1, 2):
            continue  # still genuinely in progress (STARTING/ACTIVE/ENDING) - leave it alone

        if info is not None and info["location"]:
            session.recording_url = info["location"]
            action, reason = "video.recording_completed", "recovered from LiveKit after a lost webhook"
        else:
            session.recording_url = RECORDING_FAILED_MARKER
            action = "video.recording_failed"
            reason = "egress_ended webhook never arrived and LiveKit reports no recoverable file"
        session.recording_egress_id = None
        db.commit()
        swept += 1
        _invalidate_video_sessions_cache(session.account_id)
        log_event(
            db, actor_id=session.account_id, action=action,
            target_type="video_session", target_id=session.id,
            metadata={"room_name": session.room_name, "reason": reason},
        )
    return {"swept": swept}


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
        callback_preference=qualification.get("callback_preference"),
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    _invalidate_receptionist_calls_cache(owner.account_id)
    log_event(
        db, actor_id=owner.account_id, action="receptionist.call_captured",
        target_type="receptionist_call", target_id=call.id,
        metadata={"urgency": urgency.value if urgency else None, "guardrail_flags": guardrail_flags, "is_likely_spam": is_likely_spam},
    )
    return call


def get_receptionist_call(db: Session, receptionist_call_id: str) -> ReceptionistCall | None:
    return db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()


def needs_receptionist_followup(call: ReceptionistCall) -> bool:
    """Bounded multi-turn trigger (Roadmap §7): ask exactly one follow-up
    question when a real qualification pass ran (model_version is only set
    when AI-processing consent was on file AND Groq actually returned a
    result - see capture_receptionist_call) but still got neither a name
    nor a reason - the two fields a human receptionist would never let a
    caller hang up without. Without a completed qualification pass (no
    consent, or a Groq outage), asking a follow-up is pointless - the
    follow-up's own re-qualification attempt would fail identically, so
    this correctly falls through to closing out normally instead of
    wasting a turn. followup_asked guards against ever asking twice for
    the same call, even if /respond were somehow hit again for the same
    call_sid."""
    return (
        call.model_version is not None
        and call.caller_name is None
        and call.reason is None
        and not call.followup_asked
    )


def record_receptionist_followup(db: Session, call: ReceptionistCall, followup_transcript: str) -> ReceptionistCall:
    """Merges the caller's answer to the one bounded follow-up question
    into the same call row (never a second ReceptionistCall) - re-runs
    qualify_caller() on the combined transcript so the LLM sees full
    context rather than trying to heuristically slot-fill just the new
    utterance. Same Groq-outage degrade-gracefully posture as
    capture_receptionist_call: a failure here still finalizes the call
    with whatever was already captured, never breaks the live response.
    A caller who says nothing (timeout) still bounds the attempt - the
    call finalizes with whatever the first Gather captured.
    """
    call.followup_asked = True
    if not followup_transcript:
        db.commit()
        db.refresh(call)
        return call

    call.raw_transcript = f"{call.raw_transcript}\n{followup_transcript}"

    qualification = None
    try:
        qualification, model_version = qualify_caller(db, call.account_id, call.raw_transcript)
    except LLMError:
        model_version = None

    if qualification:
        urgency_raw = qualification.get("urgency")
        urgency = ReceptionistUrgency(urgency_raw) if urgency_raw in ("low", "medium", "high") else call.urgency
        guardrail_flags = check_for_disallowed_commitments(qualification.get("summary"), qualification.get("reason"))
        is_likely_spam = bool(qualification.get("is_likely_spam"))

        call.caller_name = qualification.get("name") or call.caller_name
        call.caller_company = qualification.get("company") or call.caller_company
        call.reason = qualification.get("reason") or call.reason
        call.summary = qualification.get("summary") or call.summary
        call.urgency = urgency
        call.guardrail_flags = guardrail_flags
        call.is_likely_spam = is_likely_spam
        call.spam_reason = qualification.get("spam_reason") if is_likely_spam else None
        call.model_version = model_version
        call.callback_preference = qualification.get("callback_preference") or call.callback_preference

    db.commit()
    db.refresh(call)
    _invalidate_receptionist_calls_cache(call.account_id)
    log_event(
        db, actor_id=call.account_id, action="receptionist.call_followup_captured",
        target_type="receptionist_call", target_id=call.id, metadata={},
    )
    return call


def record_receptionist_callback_request(db: Session, call: ReceptionistCall, window: ReceptionistCallbackWindow | None) -> None:
    """Self-service booking (Roadmap §7), scoped to what this platform
    actually models: a caller-selected callback window, not a real
    calendar slot - see ReceptionistCallbackWindow's docstring. Fires from
    a live Twilio webhook, so the best-effort notification below must
    never break the caller's TwiML response."""
    call.callback_requested = True
    call.callback_window = window
    db.commit()
    _invalidate_receptionist_calls_cache(call.account_id)
    log_event(
        db, actor_id=call.account_id, action="receptionist.callback_requested",
        target_type="receptionist_call", target_id=call.id,
        metadata={"callback_window": window.value if window else None},
    )

    owner = db.query(User).filter(User.account_id == call.account_id, User.role == UserRole.OWNER).first()
    if owner is None:
        return
    try:
        notify_receptionist_callback_requested(
            db, account_id=call.account_id, account_email=owner.email,
            caller_number=call.caller_number, callback_window=window.value if window else "unspecified",
        )
    except Exception:
        pass


def mark_receptionist_call_escalated(db: Session, receptionist_call_id: str, escalated_to_user_id: str) -> None:
    call = db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()
    if call is None:
        return
    call.escalated = True
    db.commit()
    _invalidate_receptionist_calls_cache(call.account_id)
    # Roadmap §7 Evidence: "escalation path" - who this specific urgent call
    # was routed to, not just that some escalation happened.
    log_event(
        db, actor_id=call.account_id, action="receptionist.call_escalated",
        target_type="receptionist_call", target_id=call.id,
        metadata={"escalated_to_user_id": escalated_to_user_id},
    )


def mark_receptionist_escalation_missed(db: Session, receptionist_call_id: str) -> None:
    """Called from receptionist.escalation_fallback when a HIGH-urgency
    call's escalation dial resolves to anything other than "completed" -
    the human never picked up. `escalated` stays True (the platform DID
    attempt the escalation) - this only records that the attempt went
    unanswered, so staff reviewing the call summary can see it fell
    through to voicemail rather than assuming a human actually handled it.

    Real gap fix: this used to only log_event, with nothing ever telling
    the specific team member (owner.escalation_user_id) an urgent call was
    routed to them and they missed it - they'd only find out by noticing
    the voicemail the account owner gets. voice.missed_call was seeded but
    had zero call sites anywhere in this codebase; this is the one place a
    genuinely unambiguous "this exact person didn't pick up" signal exists
    (see notify_missed_call's docstring for why it's not wired more
    broadly)."""
    call = db.query(ReceptionistCall).filter(ReceptionistCall.id == receptionist_call_id).first()
    if call is None:
        return
    log_event(
        db, actor_id=call.account_id, action="receptionist.escalation_missed",
        target_type="receptionist_call", target_id=call.id,
    )

    number = db.query(PhoneNumber).filter(PhoneNumber.id == call.phone_number_id).first()
    if number is not None and number.escalation_user_id:
        target = db.query(User).filter(User.id == number.escalation_user_id).first()
        if target is not None:
            notify_missed_call(
                db, account_id=call.account_id, recipient_email=target.email,
                e164=number.e164, from_number=call.caller_number,
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
    _invalidate_receptionist_calls_cache(call.account_id)
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
    _invalidate_receptionist_calls_cache(call.account_id)
    log_event(
        db, actor_id=user.id, action="receptionist.call_summary_edited",
        target_type="receptionist_call", target_id=call.id, metadata={"edited": True},
    )
    return call


def list_account_receptionist_calls(db: Session, user: User) -> list[ReceptionistCall]:
    """Owner/Admin see every receptionist call on the account. A plain Member
    only sees calls on numbers assigned to them."""
    ids = assigned_number_ids(db, user)
    query = db.query(ReceptionistCall).filter(ReceptionistCall.account_id == user.account_id)
    if ids is not None:
        # Member view - always queries live, same reason as list_account_calls.
        return query.filter(ReceptionistCall.phone_number_id.in_(ids)).order_by(ReceptionistCall.created_at.desc()).all()

    cache_key = _receptionist_calls_cache_key(user.account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_receptionist_call(row) for row in cached]
    calls = query.order_by(ReceptionistCall.created_at.desc()).all()
    cache_set(cache_key, [_serialize_receptionist_call(c) for c in calls], ttl_seconds=_RECEPTIONIST_CALLS_CACHE_TTL_SECONDS)
    return calls


def _receptionist_calls_cache_key(account_id: str) -> str:
    return f"receptionist_calls:list:{account_id}"


_RECEPTIONIST_CALLS_CACHE_TTL_SECONDS = 15


def _serialize_receptionist_call(c: ReceptionistCall) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "phone_number_id": c.phone_number_id,
        "call_sid": c.call_sid,
        "caller_number": c.caller_number,
        "raw_transcript": c.raw_transcript,
        "caller_name": c.caller_name,
        "caller_company": c.caller_company,
        "reason": c.reason,
        "summary": c.summary,
        "urgency": c.urgency.value if c.urgency else None,
        "escalated": c.escalated,
        "assigned_user_id": c.assigned_user_id,
        "guardrail_flags": c.guardrail_flags,
        "original_summary": c.original_summary,
        "edited_at": c.edited_at.isoformat() if c.edited_at else None,
        "edited_by_user_id": c.edited_by_user_id,
        "model_version": c.model_version,
        "is_likely_spam": c.is_likely_spam,
        "spam_reason": c.spam_reason,
        "callback_preference": c.callback_preference,
        "followup_asked": c.followup_asked,
        "callback_requested": c.callback_requested,
        "callback_window": c.callback_window.value if c.callback_window else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _deserialize_receptionist_call(data: dict) -> ReceptionistCall:
    return ReceptionistCall(
        id=data["id"],
        account_id=data["account_id"],
        phone_number_id=data["phone_number_id"],
        call_sid=data["call_sid"],
        caller_number=data["caller_number"],
        raw_transcript=data["raw_transcript"],
        caller_name=data["caller_name"],
        caller_company=data["caller_company"],
        reason=data["reason"],
        summary=data["summary"],
        urgency=ReceptionistUrgency(data["urgency"]) if data["urgency"] else None,
        escalated=data["escalated"],
        assigned_user_id=data["assigned_user_id"],
        guardrail_flags=data["guardrail_flags"],
        original_summary=data["original_summary"],
        edited_at=datetime.fromisoformat(data["edited_at"]) if data["edited_at"] else None,
        edited_by_user_id=data["edited_by_user_id"],
        model_version=data["model_version"],
        is_likely_spam=data["is_likely_spam"],
        spam_reason=data["spam_reason"],
        callback_preference=data.get("callback_preference"),
        followup_asked=data.get("followup_asked", False),
        callback_requested=data.get("callback_requested", False),
        callback_window=ReceptionistCallbackWindow(data["callback_window"]) if data.get("callback_window") else None,
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_receptionist_calls_cache(account_id: str) -> None:
    cache_delete(_receptionist_calls_cache_key(account_id))
