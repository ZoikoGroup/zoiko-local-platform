"""
Voice Routing — wired to real Account/Number data (Stage 3). Inbound webhooks
are signature-verified and every call (recognized or not) is persisted via
media.service.record_call(); outbound calls require an authenticated account
that actually owns the `from_number` being used.

Only calls into app.integrations.telecom.twilio (the Provider Gateway) —
never imports the twilio SDK directly, per the Provider Gateway rule.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.integrations.telecom import twilio as telecom
from app.ops.service import KillSwitchTrippedError
from app.integrations.telecom.twilio import TelecomError
from app.media import service as media_service
from app.media.models import CallDirection
from app.numbering.identity.models import User
from app.numbering.numbers import service as numbers_service
from app.queues import service as queues_service
from app.risk import service as risk_service
from app.routing import service as routing_service
from app.routing.models import CallFlowVersion
from app.routing.service import ResolvedAction

router = APIRouter(prefix="/media/voice", tags=["voice"])


def _inner_verbs(twiml: str) -> str:
    """Strips a full TwiML document down to just the verbs inside its
    <Response> - used to splice one document's verbs into another (the
    queue node's overflow, appended right after </Enqueue> so Twilio's
    <Leave/> has somewhere to fall through to)."""
    start = twiml.index("<Response>") + len("<Response>")
    end = twiml.rindex("</Response>")
    return twiml[start:end]


def _flow_response(action: ResolvedAction, version: CallFlowVersion, request: Request, to_number: str) -> str:
    """Turns a resolved call-flow node (Advanced IVR builder, Phase 3) into
    TwiML - the flow equivalent of incoming_call's own if/elif chain below,
    just driven by a node graph instead of the four legacy PhoneNumber
    columns."""
    base = str(request.base_url)
    if action.kind == "menu":
        action_url = f"{base}media/voice/flow-menu-input?flow_version_id={version.id}&node_id={action.node_id}"
        return telecom.build_dtmf_menu_response(action.prompt, action_url)
    if action.kind == "queue":
        # Contact-center-lite (Phase 3) - hands off to app.queues for real
        # FIFO hold + agent-pull; this module only needs to know the
        # overflow node's resolved TwiML so it can be spliced in as the
        # fallthrough for Twilio's <Leave/> (see build_enqueue_response).
        queue_name = queues_service.twilio_queue_name(action.queue_id)
        wait_url = f"{base}media/voice/queue/wait?queue_id={action.queue_id}&to_number={quote(to_number)}"
        left_action_url = f"{base}media/voice/queue/left"
        if action.overflow_node_id:
            overflow_action = routing_service.resolve_specific_node(version, action.overflow_node_id)
            overflow_twiml = _flow_response(overflow_action, version, request, to_number)
        else:
            overflow_twiml = telecom.build_record_response(base + "media/voicemail/recording-complete")
        return telecom.build_enqueue_response(queue_name, wait_url, left_action_url, _inner_verbs(overflow_twiml))
    if action.kind == "forward":
        fallback_url = f"{base}media/voice/flow-forward-fallback?flow_version_id={version.id}&node_id={action.node_id}"
        status_callback_url = base + "media/voice/status-callback"
        return telecom.build_ring_group_response(action.destinations, fallback_url, status_callback_url)
    if action.kind == "ai_receptionist":
        action_url = base + "media/receptionist/respond"
        return telecom.build_gather_response(
            "Thanks for calling. You're speaking with an automated assistant, not a person. "
            "Please tell us your name, company, the reason for your call, and whether "
            "it's urgent, after the tone.",
            action_url,
        )
    if action.kind == "hangup":
        return telecom.build_say_response(action.message) if action.message else telecom.build_empty_response()
    # "voicemail" and any defensive fallback from routing.service._resolve()
    callback_url = base + "media/voicemail/recording-complete"
    return telecom.build_record_response(callback_url)


class OutboundCallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: str
    from_number: str = Field(alias="from")
    message: str = "This is a call from Zoiko Local."


class BridgeCallRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: str
    from_number: str = Field(alias="from")
    agent_number: str


def _ai_receptionist_greeting_twiml(request: Request) -> str:
    action_url = str(request.base_url) + "media/receptionist/respond"
    return telecom.build_gather_response(
        "Thanks for calling. You're speaking with an automated assistant, not a person. "
        "Please tell us your name, company, the reason for your call, and whether "
        "it's urgent, after the tone.",
        action_url,
    )


def _default_call_twiml(request: Request, db: Session, owner, to_number: str) -> str:
    """The number's normal (non-IVR) call handling: ring group/forwarding,
    then AI receptionist, then voicemail, then "unrecognized number." Used
    both when no IVR menu is configured at all, and as the fallback for an
    IVR menu that got no input or an unrecognized digit (see /ivr-select
    and /ivr-no-input below) - a number with an IVR menu that isn't
    answered still ends up exactly where it would have without one.
    """
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
        return telecom.build_ring_group_response(
            destinations, fallback_action_url, status_callback_url, recording_callback_url
        )
    elif owner is not None and owner.ai_receptionist_enabled:
        return _ai_receptionist_greeting_twiml(request)
    elif owner is not None:
        callback_url = str(request.base_url) + "media/voicemail/recording-complete"
        return telecom.build_record_response(callback_url)
    else:
        return telecom.build_say_response(
            "Thanks for calling Zoiko Local. This number isn't recognized."
        )


@router.post("/incoming")
async def incoming_call(request: Request, db: Session = Depends(get_db)):
    """Twilio hits this as a webhook when someone calls a number we own. If
    an IVR menu is configured, it's offered first (see _default_call_twiml
    for what happens after: on a matched digit, no input, or an
    unrecognized digit). Otherwise: forwarding/ring group, then AI
    receptionist, then voicemail. The call is attributed to the owning
    account (or logged as unrecognized) via the persisted CallRecord
    regardless of which branch runs.
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

    live_flow_version = routing_service.get_live_version(db, owner) if owner is not None else None

    if live_flow_version is not None:
        # Advanced IVR builder (Phase 3) - a number with an assigned, published
        # call flow is routed entirely through it, bypassing the legacy
        # forwarding/ring-group/AI-receptionist/voicemail branches below.
        # Unassigned numbers (call_flow_id is NULL, true for every number
        # that existed before this feature) are completely unaffected.
        action = routing_service.resolve_entry(live_flow_version)
        twiml = _flow_response(action, live_flow_version, request, to_number)
    elif owner is not None and owner.ivr_greeting:
        # Enhanced business routing (Phase 2) - a simpler single-level DTMF
        # menu, still available for any number that hasn't opted into the
        # full Phase 3 call-flow builder above.
        action_url = str(request.base_url) + "media/voice/ivr-select"
        no_input_url = str(request.base_url) + "media/voice/ivr-no-input"
        twiml = telecom.build_ivr_menu_response(owner.ivr_greeting, action_url, no_input_url)
    else:
        twiml = _default_call_twiml(request, db, owner, to_number)
    return Response(content=twiml, media_type="application/xml")


@router.post("/ivr-select")
async def ivr_select(request: Request, db: Session = Depends(get_db)):
    """Twilio requests this as the IVR <Gather>'s `action` URL once a digit
    is pressed (see telecom.build_ivr_menu_response). An unrecognized digit
    (or an empty Digits value from an interrupted call) falls through to
    the number's normal call handling, same as no IVR menu at all."""
    params = await media_service.verify_twilio_webhook(request)
    to_number = params.get("To", "")
    digit = params.get("Digits", "")
    owner = media_service.find_number_owner(db, to_number)

    option = None
    if owner is not None and digit:
        _, options = numbers_service.get_ivr_menu(db, to_number)
        option = next((o for o in options if o.digit == digit), None)

    if option is None:
        twiml = _default_call_twiml(request, db, owner, to_number)
    else:
        status_callback_url = str(request.base_url) + "media/voice/status-callback"
        fallback_action_url = str(request.base_url) + "media/voice/forward-fallback"
        recording_callback_url = (
            str(request.base_url) + "media/voice/recording-callback"
            if owner is not None and media_service.should_record_forwarded_call(db, owner.account_id)
            else None
        )
        twiml = telecom.build_ring_group_response(
            [option.destination_number], fallback_action_url, status_callback_url, recording_callback_url
        )
    return Response(content=twiml, media_type="application/xml")


@router.post("/ivr-no-input")
async def ivr_no_input(request: Request, db: Session = Depends(get_db)):
    """Twilio redirects here when the IVR <Gather> times out with no digit
    pressed at all (see telecom.build_ivr_menu_response's docstring - this
    case never reaches /ivr-select, since Twilio only calls a Gather's
    action URL when there was at least some input)."""
    params = await media_service.verify_twilio_webhook(request)
    to_number = params.get("To", "")
    owner = media_service.find_number_owner(db, to_number)
    twiml = _default_call_twiml(request, db, owner, to_number)
    return Response(content=twiml, media_type="application/xml")


@router.post("/outbound")
async def outbound_call(
    body: OutboundCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
    x_device_fingerprint: str | None = Header(default=None),
):
    # Architecture doc §5 "Fraud and Risk: device fingerprinting" - detection
    # only, never blocks the call (see check_fingerprint_on_call's
    # docstring). Recorded up front, before place_outbound_call, so even a
    # call this account isn't allowed to make (destination blocked, velocity
    # limit, etc.) still counts as this device touching this account - that
    # association is the signal, independent of whether the call itself
    # goes through.
    risk_service.check_fingerprint_on_call(db, fingerprint_hash=x_device_fingerprint, account_id=current_user.account_id)

    status_callback_url = str(request.base_url) + "media/voice/status-callback"
    try:
        return media_service.place_outbound_call(
            db, current_user, body.to, body.from_number, body.message, status_callback_url
        )
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    # BillingSuspendedError no longer caught here - subclasses
    # EntitlementError, handled by the global entitlement_error_handler.
    except risk_service.DestinationBlockedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except risk_service.VelocityLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.ConcurrentCallLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.GeographicDispersionError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.SpendLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.CumulativeTrialUsageExceededError as e:
        raise HTTPException(status_code=402, detail=str(e)) from e
    except risk_service.AccountKillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/bridge")
async def bridge_call(
    body: BridgeCallRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
    x_device_fingerprint: str | None = Header(default=None),
):
    """Live two-way calling ("click to call"): rings the caller's own
    configured forwarding number first; once they answer, /bridge-connect
    dials the real customer and joins both legs - see
    media.service.place_bridge_call's docstring for the full flow. Unlike
    /outbound (a one-way announcement), the caller genuinely talks to
    whoever picks up on the other end."""
    risk_service.check_fingerprint_on_call(db, fingerprint_hash=x_device_fingerprint, account_id=current_user.account_id)

    status_callback_url = str(request.base_url) + "media/voice/status-callback"
    bridge_connect_url = (
        str(request.base_url) + "media/voice/bridge-connect"
        f"?to={quote(body.to)}&from={quote(body.from_number)}"
    )
    try:
        return media_service.place_bridge_call(
            db, current_user, body.from_number, body.to, body.agent_number, bridge_connect_url, status_callback_url
        )
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except risk_service.DestinationBlockedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except risk_service.VelocityLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.ConcurrentCallLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.GeographicDispersionError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.SpendLimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e)) from e
    except risk_service.CumulativeTrialUsageExceededError as e:
        raise HTTPException(status_code=402, detail=str(e)) from e
    except risk_service.AccountKillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/bridge-connect")
async def bridge_connect(request: Request, to: str, from_: str = Query(alias="from")):
    """Twilio requests this once the agent leg (place_bridge_call's call to
    owner.forwarding_number) is actually answered - never called at all if
    the agent doesn't pick up, so the customer is never dialed for a call
    the agent never joined. No signature verification/DB lookup needed:
    `to`/`from` are values WE put on this URL when creating the call (see
    bridge_call above), not caller-supplied input Twilio is relaying."""
    status_callback_url = str(request.base_url) + "media/voice/status-callback"
    twiml = telecom.build_bridge_response(to, caller_id=from_, status_callback_url=status_callback_url)
    return Response(content=twiml, media_type="application/xml")


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


@router.post("/flow-menu-input")
async def flow_menu_input(request: Request, db: Session = Depends(get_db)):
    """Twilio's action URL for a call-flow MENU node's <Gather> (Advanced
    IVR builder, Phase 3) - see routing.service.build_dtmf_menu_response's
    action_url. `flow_version_id`/`node_id` identify exactly which
    published version and which menu node gathered this input; `Digits` is
    absent when the caller pressed nothing before the gather timed out,
    which resolve_menu_input() treats as the menu's timeout_node_id."""
    params = await media_service.verify_twilio_webhook(request)
    version_id = request.query_params.get("flow_version_id", "")
    node_id = request.query_params.get("node_id", "")
    version = routing_service.get_version_by_id(db, version_id)
    if version is None:
        return Response(
            content=telecom.build_say_response("Sorry, this call flow is no longer available."),
            media_type="application/xml",
        )
    action = routing_service.resolve_menu_input(version, node_id, params.get("Digits") or None)
    return Response(content=_flow_response(action, version, request, params.get("To", "")), media_type="application/xml")


@router.post("/flow-forward-fallback")
async def flow_forward_fallback(request: Request, db: Session = Depends(get_db)):
    """Failover (architecture doc's Call Flow Designer requirement) for a
    call-flow FORWARD node: Twilio's <Dial> action URL once the dial to
    that node's destinations resolves. A DialCallStatus of "completed"
    means the call was genuinely answered and has already ended normally -
    same overflow-handling semantics as the legacy /forward-fallback
    route, just keyed off this node's own on_no_answer_node_id instead of
    always falling back to voicemail."""
    params = await media_service.verify_twilio_webhook(request)
    if params.get("DialCallStatus") == "completed":
        return Response(content=telecom.build_empty_response(), media_type="application/xml")

    version_id = request.query_params.get("flow_version_id", "")
    node_id = request.query_params.get("node_id", "")
    version = routing_service.get_version_by_id(db, version_id)
    if version is None:
        callback_url = str(request.base_url) + "media/voicemail/recording-complete"
        return Response(content=telecom.build_record_response(callback_url), media_type="application/xml")
    action = routing_service.resolve_forward_failover(version, node_id)
    return Response(content=_flow_response(action, version, request, params.get("To", "")), media_type="application/xml")


@router.post("/forward-fallback")
async def forward_fallback(request: Request, db: Session = Depends(get_db)):
    """Twilio requests this as the forwarded/ring-group <Dial>'s `action`
    URL once the dial resolves (see telecom.build_ring_group_response).
    `DialCallStatus` is "completed" for a call that was actually answered
    and has now ended normally - nothing further to do there. Any other
    status (no-answer, busy, failed) means nobody picked up.

    Confirmed live (2026-08-22): a customer with BOTH forwarding and AI
    Receptionist configured got plain voicemail here regardless - AI never
    got a chance to catch what the human missed, even though the whole
    point of turning both on is "try a person first, let AI handle it if
    they don't answer." Falls through to the AI Receptionist greeting when
    it's enabled for this number; only a number with neither forwarding
    fully covered nor AI Receptionist on falls all the way to voicemail -
    "overflow handling" (Architecture doc Phase 2)."""
    params = await media_service.verify_twilio_webhook(request)
    if params.get("DialCallStatus") == "completed":
        return Response(content=telecom.build_empty_response(), media_type="application/xml")

    owner = media_service.find_number_owner(db, params.get("To", ""))
    if owner is not None and owner.ai_receptionist_enabled:
        return Response(content=_ai_receptionist_greeting_twiml(request), media_type="application/xml")

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
