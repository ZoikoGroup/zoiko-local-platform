"""
Provider Gateway for Twilio (telecom category). Per CLAUDE.md's Provider Gateway
rule, this is the ONLY file allowed to import the `twilio` SDK directly —
everything else in the app calls the functions below instead.

Groundwork built ahead of Stage 1 finishing (see CLAUDE.md's 2026-07-30
exception note). No Account/Number model linkage, no audit logging, no
entitlement checks — that gets added once Stage 1/2 land properly.
"""

from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

from app.core.config import settings

_NUMBER_TYPE_PATH = {"local": "Local", "mobile": "Mobile", "tollfree": "TollFree"}


class TelecomError(Exception):
    """Raised instead of letting TwilioRestException escape this module —
    callers elsewhere in the app should never need to know or catch a
    vendor-specific exception type (Provider Gateway rule)."""


def _client() -> Client:
    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


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
        resource = getattr(_client().available_phone_numbers(country), _NUMBER_TYPE_PATH[number_type].lower())
        numbers = resource.list(**kwargs)
    except TwilioRestException as e:
        if e.status == 404:
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
        numbers = _client().incoming_phone_numbers.list()
    except TwilioRestException as e:
        raise TelecomError(str(e)) from e
    return [{"sid": n.sid, "phone_number": n.phone_number, "capabilities": n.capabilities} for n in numbers]


def buy_number(phone_number: str) -> dict:
    """Not yet exercised against a real purchase (see docs/stage2-twilio-numbering-notes.md
    — deliberately skipped to avoid spending trial credit). Written directly
    against the documented IncomingPhoneNumbers create contract.
    """
    try:
        number = _client().incoming_phone_numbers.create(phone_number=phone_number)
    except TwilioRestException as e:
        raise TelecomError(str(e)) from e
    return {"sid": number.sid, "phone_number": number.phone_number, "capabilities": number.capabilities}


def place_call(to: str, from_: str, twiml_url: str | None = None, twiml: str | None = None) -> dict:
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

    try:
        call = _client().calls.create(**kwargs)
    except TwilioRestException as e:
        raise TelecomError(str(e)) from e
    return {"sid": call.sid, "status": call.status, "to": call.to, "from": call.from_}


def get_call(call_sid: str) -> dict:
    try:
        call = _client().calls(call_sid).fetch()
    except TwilioRestException as e:
        raise TelecomError(str(e)) from e
    return {"sid": call.sid, "status": call.status, "to": call.to, "from": call.from_, "duration": call.duration}


def list_calls(limit: int = 20) -> list[dict]:
    """Read-only, confirmed live-working with zero owned numbers and zero
    calls made (returns an empty list, not an error).
    """
    try:
        calls = _client().calls.list(limit=limit)
    except TwilioRestException as e:
        raise TelecomError(str(e)) from e
    return [{"sid": c.sid, "status": c.status, "to": c.to, "from": c.from_} for c in calls]


def build_say_response(message: str) -> str:
    """Builds TwiML for a simple spoken response. Wraps the vendor's TwiML
    builder so callers elsewhere in the app never import `twilio` directly.
    """
    response = VoiceResponse()
    response.say(message)
    return str(response)
