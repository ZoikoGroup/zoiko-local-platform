"""
Provider Gateway for Twilio (telecom category). Per CLAUDE.md's Provider Gateway
rule, this is the ONLY file allowed to import the `twilio` SDK directly —
everything else in the app calls the functions below instead.

Groundwork built ahead of Stage 1 finishing (see CLAUDE.md's 2026-07-30
exception note). No Account/Number model linkage, no audit logging, no
entitlement checks — that gets added once Stage 1/2 land properly.
"""

import httpx
from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

from app.core.config import settings
from app.observability.service import trace_provider_call

_NUMBER_TYPE_PATH = {"local": "Local", "mobile": "Mobile", "tollfree": "TollFree"}


class TelecomError(Exception):
    """Raised instead of letting TwilioRestException escape this module —
    callers elsewhere in the app should never need to know or catch a
    vendor-specific exception type (Provider Gateway rule)."""


def _client() -> Client:
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def health_check() -> dict:
    """Real reachability check, not just "is a key present" - fetches the
    account resource, the cheapest authenticated call Twilio offers."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        return {"configured": False, "ok": False, "detail": None}
    try:
        _client().api.accounts(settings.twilio_account_sid).fetch()
        return {"configured": True, "ok": True, "detail": None}
    except TwilioException as e:
        return {"configured": True, "ok": False, "detail": str(e)}


def send_sms(to: str, body: str) -> dict:
    """Sends a system SMS notification from Zoiko's own notification
    number - NOT tied to any customer's purchased number. Distinct from
    voice.py's customer-facing calling features."""
    if not settings.twilio_trial_number:
        raise TelecomError("No Twilio notification number configured (TWILIO_TRIAL_NUMBER)")
    try:
        with trace_provider_call("twilio", "send_sms"):
            message = _client().messages.create(to=to, from_=settings.twilio_trial_number, body=body)
    except TwilioException as e:
        raise TelecomError(str(e)) from e
    return {"sid": message.sid, "status": message.status}


def search_available_numbers(country: str, number_type: str = "local", area_code: str | None = None,
                              contains: str | None = None, limit: int = 10) -> list[dict]:
    """See docs/stage2-twilio-numbering-notes.md for the behavior this wraps:
    no reservation/hold semantics, unsupported country raises (caller should
    catch TwilioRestException and treat 404 as "not supported"), empty result
    is a normal outcome, not an error.
    """
    kwargs = {"limit": limit}
    if area_code:
        kwargs["area_code"] = area_code
    if contains:
        kwargs["contains"] = contains

    try:
        with trace_provider_call("twilio", "search_available_numbers"):
            resource = getattr(_client().available_phone_numbers(country), _NUMBER_TYPE_PATH[number_type].lower())
            numbers = resource.list(**kwargs)
    except TwilioException as e:
        # Twilio's SDK raises the bare base class (no .status) for some
        # lower-level failures (e.g. missing/invalid credentials) rather
        # than the TwilioRestException subclass this 404 check expects.
        if isinstance(e, TwilioRestException) and e.status == 404:
            raise TelecomError(f"Twilio has no {number_type} numbering coverage for country '{country}'") from e
        raise TelecomError(str(e)) from e

    return [
        {
            "phone_number": n.phone_number,
            "locality": n.locality,
            "region": n.region,
            "capabilities": n.capabilities,
            "address_requirements": n.address_requirements,
        }
        for n in numbers
    ]


def list_owned_numbers() -> list[dict]:
    try:
        with trace_provider_call("twilio", "list_owned_numbers"):
            numbers = _client().incoming_phone_numbers.list()
    except TwilioException as e:
        raise TelecomError(str(e)) from e
    return [{"sid": n.sid, "phone_number": n.phone_number, "capabilities": n.capabilities} for n in numbers]


def set_voice_webhook(phone_number_sid: str, public_base_url: str) -> None:
    """(Re)points an already-purchased number's voice webhook + status
    callback at the given base URL - needed whenever PUBLIC_BASE_URL changes
    (e.g. a new ngrok tunnel in dev), since buy_number() only sets these at
    purchase time.
    """
    try:
        with trace_provider_call("twilio", "set_voice_webhook"):
            _client().incoming_phone_numbers(phone_number_sid).update(
                voice_url=f"{public_base_url}/media/voice/incoming",
                voice_method="POST",
                status_callback=f"{public_base_url}/media/voice/status-callback",
                status_callback_method="POST",
            )
    except TwilioException as e:
        raise TelecomError(str(e)) from e


def release_number(phone_number_sid: str) -> None:
    """Actually releases a purchased number back to Twilio - without this,
    cancelling a number in our own DB leaves it sitting active (and billing)
    on the real Twilio account forever."""
    try:
        with trace_provider_call("twilio", "release_number"):
            _client().incoming_phone_numbers(phone_number_sid).delete()
    except TwilioException as e:
        raise TelecomError(str(e)) from e


def buy_number(phone_number: str) -> dict:
    """Written directly against the documented IncomingPhoneNumbers create
    contract. Confirmed live against a real Twilio trial account.

    Registers our own voice webhook (the URL Twilio actually calls when
    someone dials this number - without it, a purchased number never reaches
    /media/voice/incoming at all) plus a status-callback URL for the final
    completed/duration event, both only when a public base URL is configured
    (nothing to point at otherwise, e.g. before ngrok is running in dev).
    """
    kwargs = {"phone_number": phone_number}
    if settings.public_base_url:
        kwargs["voice_url"] = f"{settings.public_base_url}/media/voice/incoming"
        kwargs["voice_method"] = "POST"
        kwargs["status_callback"] = f"{settings.public_base_url}/media/voice/status-callback"
        kwargs["status_callback_method"] = "POST"

    try:
        with trace_provider_call("twilio", "buy_number"):
            number = _client().incoming_phone_numbers.create(**kwargs)
    except TwilioException as e:
        raise TelecomError(str(e)) from e
    return {"sid": number.sid, "phone_number": number.phone_number, "capabilities": number.capabilities}


def place_call(
    to: str, from_: str, twiml_url: str | None = None, twiml: str | None = None,
    status_callback_url: str | None = None,
) -> dict:
    """`from_` must be a Twilio number owned on this account. Confirmed live:
    Twilio rejects with a 400 ("not yet verified for your account") if `from_`
    isn't an owned/verified number — see docs/stage3-twilio-calling-notes.md.
    """
    if not twiml_url and not twiml:
        raise ValueError("Provide either twiml_url or twiml")

    kwargs = {"to": to, "from_": from_}
    if twiml_url:
        kwargs["url"] = twiml_url
    else:
        kwargs["twiml"] = twiml
    if status_callback_url:
        kwargs["status_callback"] = status_callback_url
        kwargs["status_callback_event"] = ["completed"]
        kwargs["status_callback_method"] = "POST"

    try:
        with trace_provider_call("twilio", "place_call"):
            call = _client().calls.create(**kwargs)
    except TwilioException as e:
        raise TelecomError(str(e)) from e
    return {"sid": call.sid, "status": call.status, "to": call.to, "from": call.from_}


def get_call(call_sid: str) -> dict:
    try:
        with trace_provider_call("twilio", "get_call"):
            call = _client().calls(call_sid).fetch()
    except TwilioException as e:
        raise TelecomError(str(e)) from e
    return {"sid": call.sid, "status": call.status, "to": call.to, "from": call.from_, "duration": call.duration}


def list_calls(limit: int = 20) -> list[dict]:
    """Read-only, confirmed live-working with zero owned numbers and zero
    calls made (returns an empty list, not an error).
    """
    try:
        with trace_provider_call("twilio", "list_calls"):
            calls = _client().calls.list(limit=limit)
    except TwilioException as e:
        raise TelecomError(str(e)) from e
    return [{"sid": c.sid, "status": c.status, "to": c.to, "from": c.from_} for c in calls]


def build_say_response(message: str) -> str:
    """Builds TwiML for a simple spoken response. Wraps the vendor's TwiML
    builder so callers elsewhere in the app never import `twilio` directly.
    """
    response = VoiceResponse()
    response.say(message)
    return str(response)


def build_forward_response(
    forwarding_number: str,
    status_callback_url: str | None = None,
    recording_callback_url: str | None = None,
) -> str:
    """Builds TwiML that forwards (dials out) the incoming call to another
    number. When recording_callback_url is given, records the whole
    two-way conversation from the moment it's answered (both legs mixed
    into one track) and posts the finished recording there.
    """
    response = VoiceResponse()
    dial_kwargs = {}
    if status_callback_url:
        dial_kwargs["action"] = status_callback_url
        dial_kwargs["status_callback"] = status_callback_url
        dial_kwargs["status_callback_event"] = "completed"
    if recording_callback_url:
        dial_kwargs["record"] = "record-from-answer-dual"
        dial_kwargs["recording_status_callback"] = recording_callback_url
        dial_kwargs["recording_status_callback_method"] = "POST"
        dial_kwargs["recording_status_callback_event"] = "completed"
    response.dial(forwarding_number, **dial_kwargs)
    return str(response)


def build_empty_response() -> str:
    """No further instructions - Twilio hangs up. Used as the <Dial>
    action's reply when DialCallStatus is "completed" (the call was
    genuinely answered and has already ended normally)."""
    return str(VoiceResponse())


def build_ring_group_response(
    destinations: list[str],
    fallback_action_url: str,
    status_callback_url: str | None = None,
    recording_callback_url: str | None = None,
) -> str:
    """Enhanced business routing (Architecture doc Phase 2) - a superset of
    build_forward_response: rings every destination in `destinations`
    simultaneously (multiple <Number> children under one <Dial> - Twilio's
    native ring-group primitive, first to answer wins, the rest stop
    ringing) instead of a single number. `fallback_action_url` is always
    set (unlike build_forward_response's optional action) - see
    voice.py's /forward-fallback route, which routes to voicemail only
    when the dial genuinely wasn't answered."""
    response = VoiceResponse()
    dial_kwargs = {"action": fallback_action_url}
    if status_callback_url:
        dial_kwargs["status_callback"] = status_callback_url
        dial_kwargs["status_callback_event"] = "completed"
    if recording_callback_url:
        dial_kwargs["record"] = "record-from-answer-dual"
        dial_kwargs["recording_status_callback"] = recording_callback_url
        dial_kwargs["recording_status_callback_method"] = "POST"
        dial_kwargs["recording_status_callback_event"] = "completed"
    dial = response.dial(**dial_kwargs)
    for destination in destinations:
        dial.number(destination)
    return str(response)


def build_gather_response(prompt: str, action_url: str) -> str:
    """Builds TwiML for the AI Receptionist's single free-form capture: Twilio
    transcribes the caller's speech itself (no vendor call needed for this
    part) and POSTs the SpeechResult to `action_url`."""
    response = VoiceResponse()
    gather = response.gather(input="speech", action=action_url, method="POST", speech_timeout="auto", timeout=8)
    gather.say(prompt)
    response.say("We didn't catch that. Please try calling again.")
    return str(response)


def build_receptionist_reply_response(
    message: str, forward_to: str | None = None, status_callback_url: str | None = None
) -> str:
    """Closes out the receptionist flow: a spoken reply, then either an
    escalation dial to a human or a hangup."""
    response = VoiceResponse()
    response.say(message)
    if forward_to:
        dial_kwargs = {}
        if status_callback_url:
            dial_kwargs = {
                "action": status_callback_url,
                "status_callback": status_callback_url,
                "status_callback_event": "completed",
            }
        response.dial(forward_to, **dial_kwargs)
    else:
        response.hangup()
    return str(response)


def build_record_response(callback_url: str) -> str:
    """Builds TwiML that prompts the caller and records a voicemail, POSTing
    the result to `callback_url` once recording finishes."""
    response = VoiceResponse()
    response.say("Please leave a message after the tone.")
    response.record(action=callback_url, method="POST", max_length=120, play_beep=True)
    return str(response)


def download_recording(recording_url: str) -> bytes:
    """Recording media URLs require the same Basic Auth as the REST API —
    unauthenticated fetches get a 401, so this can't just be a plain GET."""
    try:
        with trace_provider_call("twilio", "download_recording"):
            response = httpx.get(
                recording_url, auth=(settings.twilio_account_sid, settings.twilio_auth_token), timeout=30.0
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Could not download recording: {e}") from e
    return response.content


def delete_recording(recording_sid: str) -> None:
    """Actually removes a recording from Twilio's storage - used once a
    voicemail/call recording is past its retention window, so the audio
    doesn't just sit there forever after we stop linking to it."""
    try:
        with trace_provider_call("twilio", "delete_recording"):
            _client().recordings(recording_sid).delete()
    except TwilioException as e:
        raise TelecomError(str(e)) from e


def recording_sid_from_url(recording_url: str) -> str:
    """Twilio's RecordingUrl webhook value is the resource URL itself
    (.../Recordings/{sid}, no file extension) - the SID is just the last
    path segment."""
    return recording_url.rstrip("/").rsplit("/", 1)[-1]


def validate_webhook_signature(url: str, params: dict, signature: str | None) -> bool:
    """Verifies a Twilio webhook actually came from Twilio (HMAC-SHA1 over the
    callback URL + POST params, per Twilio's X-Twilio-Signature scheme) —
    without this, anyone can POST fake call/recording events to our webhooks.
    """
    if not signature:
        return False
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.validate(url, params, signature)
