"""
Voice Routing — wired to real Account/Number data (Stage 3). Inbound webhooks
are signature-verified and every call (recognized or not) is persisted via
media.service.record_call(); outbound calls require an authenticated account
that actually owns the `from_number` being used.

Only calls into app.integrations.telecom.twilio (the Provider Gateway) —
never imports the twilio SDK directly, per the Provider Gateway rule.
"""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.billing import service as billing_service
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
from app.routing.models import CallFlow, CallFlowVersion
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


def _flow_response(
    db: Session, action: ResolvedAction, version: CallFlowVersion, request: Request, to_number: str, owner=None
) -> str:
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
            overflow_twiml = _flow_response(db, overflow_action, version, request, to_number, owner)
        else:
            overflow_twiml = telecom.build_record_response(base + "media/voicemail/recording-complete")
        return telecom.build_enqueue_response(queue_name, wait_url, left_action_url, _inner_verbs(overflow_twiml))
    if action.kind == "forward":
        fallback_url = f"{base}media/voice/flow-forward-fallback?flow_version_id={version.id}&node_id={action.node_id}"
        status_callback_url = base + "media/voice/status-callback"
        # Real gap fix: this FORWARD node never passed recording_callback_
        # url at all, unlike the legacy ring-group/forward path and the
        # single-level IVR menu path (both compute it via
        # should_record_forwarded_call before dialing) - any account routed
        # through a published Call Flow's FORWARD node never got its
        # forwarded calls recorded, and therefore never got an AI call
        # summary, even with AI-processing consent on file.
        call_flow = db.query(CallFlow).filter(CallFlow.id == version.call_flow_id).first()
        recording_callback_url = (
            base + "media/voice/recording-callback"
            if call_flow is not None and media_service.should_record_forwarded_call(db, call_flow.account_id)
            else None
        )
        # Also ring the browser dashboard alongside this node's configured
        # phone destinations - same rationale as the legacy forwarding path
        # in _default_call_twiml above.
        destinations = ([f"client:{owner.account_id}"] if owner is not None else []) + action.destinations
        return telecom.build_ring_group_response(
            destinations, fallback_url, status_callback_url, recording_callback_url
        )
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
    # Real gap fix: a ring group used to be completely inert unless
    # forwarding_number was ALSO set, since should_forward_call only ever
    # checked that one field - an account that configured shared call
    # handling (a ring group) but never separately filled in the legacy
    # forwarding_number field got zero forwarding, with nothing warning
    # them. Computed once, up front, so should_forward_call and the
    # destinations list below share the same query instead of one deciding
    # whether to forward and the other separately re-deriving what to ring.
    ring_group = numbers_service.list_ring_group(db, to_number) if owner is not None else []
    if owner is not None and media_service.should_forward_call(owner, has_ring_group=bool(ring_group)):
        status_callback_url = str(request.base_url) + "media/voice/status-callback"
        fallback_action_url = str(request.base_url) + "media/voice/forward-fallback"
        recording_callback_url = (
            str(request.base_url) + "media/voice/recording-callback"
            if media_service.should_record_forwarded_call(db, owner.account_id)
            else None
        )
        # Ring every configured destination simultaneously if a ring group
        # is set, otherwise fall back to the plain single forwarding_number.
        destinations = [d.destination_number for d in ring_group] or [owner.forwarding_number]
        # Also ring the browser (Call from Browser's same Twilio Client
        # identity, account_id) alongside the real phone(s), so whoever's
        # available first - a person at their desk in the dashboard, or a
        # person with their phone - picks up. Harmless if no browser tab
        # has the Device registered right now: that leg just never
        # connects, same as a phone that's switched off.
        destinations = [f"client:{owner.account_id}"] + destinations
        return telecom.build_ring_group_response(
            destinations, fallback_action_url, status_callback_url, recording_callback_url
        )
    elif owner is not None and owner.ai_receptionist_enabled:
        return _ai_receptionist_greeting_twiml(request)
    elif owner is not None and billing_service.has_entitlement(db, owner.account_id, "voicemail.enabled"):
        callback_url = str(request.base_url) + "media/voicemail/recording-complete"
        return telecom.build_record_response(callback_url)
    elif owner is not None:
        # ZL-COM-ENT-001 v3.0 - voicemail.enabled is seeded True on every
        # real plan today, so this should never actually fire in practice;
        # defense-in-depth for free_trial/enterprise (no seeded rows,
        # deny-by-default) rather than an expected path. Inside a live
        # Twilio webhook - fails closed/silent (has_entitlement above), not
        # a raise.
        return telecom.build_say_response(
            "Sorry, no one is available to take your call right now. Goodbye."
        )
    else:
        return telecom.build_say_response(
            "Thanks for calling Zoiko Local. This number isn't recognized."
        )


def _resolve_call_twiml(request: Request, db: Session, owner, to_number: str) -> str:
    """The real, full call-handling decision for a number: advanced IVR
    builder flow, then simple single-level IVR, then forwarding/ring
    group/AI receptionist/voicemail (_default_call_twiml). Shared by
    incoming_call (a real Twilio inbound webhook) and browser_connect's
    app-to-app branch (ZL-COM-ENT-001 v3.0 voice.app_to_app - a browser
    call to another Zoiko account's number routes through that account's
    real configured handling, not a bare client-to-client bridge that
    would bypass it entirely - see media.service.handle_browser_connect's
    docstring)."""
    live_flow_version = routing_service.get_live_version(db, owner) if owner is not None else None

    if live_flow_version is not None:
        # Advanced IVR builder (Phase 3) - a number with an assigned, published
        # call flow is routed entirely through it, bypassing the legacy
        # forwarding/ring-group/AI-receptionist/voicemail branches below.
        # Unassigned numbers (call_flow_id is NULL, true for every number
        # that existed before this feature) are completely unaffected.
        action = routing_service.resolve_entry(live_flow_version)
        return _flow_response(db, action, live_flow_version, request, to_number, owner)
    elif owner is not None and owner.ivr_greeting:
        # Enhanced business routing (Phase 2) - a simpler single-level DTMF
        # menu, still available for any number that hasn't opted into the
        # full Phase 3 call-flow builder above.
        action_url = str(request.base_url) + "media/voice/ivr-select"
        no_input_url = str(request.base_url) + "media/voice/ivr-no-input"
        return telecom.build_ivr_menu_response(owner.ivr_greeting, action_url, no_input_url)
    return _default_call_twiml(request, db, owner, to_number)


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

    twiml = _resolve_call_twiml(request, db, owner, to_number)
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
        destinations = [f"client:{owner.account_id}", option.destination_number] if owner is not None else [option.destination_number]
        twiml = telecom.build_ring_group_response(
            destinations, fallback_action_url, status_callback_url, recording_callback_url
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


@router.get("/browser-token")
async def browser_token(current_user: User = Depends(require_writer), db: Session = Depends(get_db)):
    """Issues a short-lived Twilio Voice access token for the browser
    calling SDK (@twilio/voice-sdk) - the frontend uses this to register a
    real Twilio Client and place calls directly from the browser, no
    separate phone involved (unlike /bridge above). Gated the same as
    every other real write action (require_writer): a Viewer shouldn't be
    able to place calls, browser or otherwise.

    Deliberately re-checks TRIALING here even though this route is a GET
    (exempt from app.core.deps.require_paid_or_read_only, since every
    other GET in this app is read-only) - a token handed out here lets the
    browser place a real call via /browser-connect, a Twilio webhook with
    no customer Authorization header at all, so the router-wide trial gate
    can never see or block that call. Without this explicit check, browser
    calling would be a real loophole around "trial accounts can view but
    not perform paid actions.\""""
    from app.billing.models import SubscriptionStatus
    from app.billing.service import TrialWriteRestrictedError, get_or_create_subscription

    sub = get_or_create_subscription(db, current_user.account_id)
    if sub.status == SubscriptionStatus.TRIALING:
        raise TrialWriteRestrictedError(
            "Upgrade your plan to use this feature - you can view it during your trial, but changes need a paid plan."
        )

    token = telecom.build_voice_access_token(identity=current_user.account_id)
    return {"token": token}


@router.post("/browser-connect")
async def browser_connect(request: Request, db: Session = Depends(get_db)):
    """Twilio calls this the instant a browser holding a token minted by
    /browser-token places a call via Device.connect({params: {To,
    ZoikoFrom}}). `Caller` here is always "client:<account_id>" - the
    identity Twilio embedded in that token when it was issued, not
    caller-supplied input being blindly relayed; the browser cannot forge
    a different identity than the one its token was minted with. Catches
    every real-call rejection reason and speaks it back instead of
    returning a JSON error, which Twilio can't render into anything a
    caller would hear - a webhook must always answer in TwiML."""
    params = await media_service.verify_twilio_webhook(request)
    account_id = params.get("Caller", "").removeprefix("client:")
    to = params.get("To", "")
    from_number = params.get("ZoikoFrom", "")
    call_sid = params.get("CallSid", "")
    status_callback_url = str(request.base_url) + "media/voice/status-callback"
    try:
        result = media_service.handle_browser_connect(
            db, account_id=account_id, from_number=from_number, to=to, call_sid=call_sid,
            status_callback_url=status_callback_url,
        )
        if result["mode"] == "app_to_app":
            # ZL-COM-ENT-001 v3.0 voice.app_to_app - route through the
            # receiving account's own real call handling, same as a real
            # inbound call to their number (see _resolve_call_twiml).
            twiml = _resolve_call_twiml(request, db, result["owner"], to)
        else:
            twiml = telecom.build_bridge_response(
                result["destination"], caller_id=result["caller_id"], status_callback_url=status_callback_url,
            )
    except (
        media_service.CallAuthorizationError,
        billing_service.EntitlementError,
        risk_service.DestinationBlockedError,
        risk_service.VelocityLimitExceededError,
        risk_service.ConcurrentCallLimitExceededError,
        risk_service.GeographicDispersionError,
        risk_service.SpendLimitExceededError,
        risk_service.CumulativeTrialUsageExceededError,
        risk_service.AccountKillSwitchTrippedError,
        KillSwitchTrippedError,
        TelecomError,
    ) as e:
        twiml = telecom.build_say_response(f"Sorry, this call could not be placed. {e}")
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
    # A bare 204 confirmed live (via this call's own Notifications) to reach
    # Twilio with an empty Content-Type header, which its webhook validator
    # rejects as error 12300 "Invalid Content-Type" - an empty TwiML
    # document is the standard, safe response shape for a callback Twilio
    # doesn't otherwise act on.
    return Response(content=telecom.build_empty_response(), media_type="application/xml")


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
    owner = media_service.find_number_owner(db, params.get("To", ""))
    return Response(
        content=_flow_response(db, action, version, request, params.get("To", ""), owner), media_type="application/xml"
    )


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
    owner = media_service.find_number_owner(db, params.get("To", ""))
    return Response(
        content=_flow_response(db, action, version, request, params.get("To", ""), owner), media_type="application/xml"
    )


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
            "recording_url": media_service.public_recording_url(c.recording_url),
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


@router.get("/calls/{call_sid}/recording")
async def get_call_recording(
    call_sid: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Streams the recording audio through this backend instead of handing
    the frontend Twilio's raw recording_url - that URL requires Twilio's
    own account credentials to fetch, which is why opening it directly in
    a browser prompts for a login instead of playing audio."""
    try:
        content, content_type = media_service.get_call_recording_media(db, current_user, call_sid)
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(content=content, media_type=content_type)


class TransferCallRequest(BaseModel):
    destination: str


@router.post("/calls/{call_sid}/transfer")
async def transfer_call(
    call_sid: str,
    payload: TransferCallRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    """ZL-COM-ENT-001 v3.0 - routing.transfer (Business+). Blind/cold
    transfer - redirects the call to a new destination, dropping the
    transferring leg. No live in-call push channel exists in this
    codebase (no websocket infra) - the frontend acts against whatever
    call state it last polled/fetched, same as the rest of the Calls UI."""
    try:
        return media_service.transfer_call(db, current_user, call_sid, payload.destination)
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except billing_service.EntitlementRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": e.key, "current_plan": e.plan_code},
        ) from e
    except media_service.CallNotTransferableError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
