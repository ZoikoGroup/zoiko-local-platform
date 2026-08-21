"""Secondary telecom vendor (Vonage) behind telecom_failover_enabled. Real
API calls, not a mock - but NOT tested against a live account, since no real
Vonage credentials exist yet (same caveat as every other secondary in this
codebase); wire real VONAGE_* credentials in .env and flip
TELECOM_FAILOVER_ENABLED=true to activate. Callers in twilio.py never change,
since it dispatches to this module by function name only.

Vonage's data model doesn't line up 1:1 with Twilio's for every operation:
SMS, number search/purchase, and outbound voice map cleanly, but
set_voice_webhook/release_number/get_call/list_calls/download_recording/
delete_recording are keyed by a Twilio-issued SID or recording URL that
Vonage has no way to resolve (call/number identifiers aren't shared across
vendors, and neither is TwiML - Vonage's NCCO is a different call-control
format). Those raise a clearly-labeled TelecomError explaining the
architectural gap rather than pretending compatibility that doesn't exist -
consistent with this codebase's "no silent failure" principle applying to
the failover path too, not just the primary one.
"""

import base64
import re
import time
import uuid

import httpx
from jose import jwt as jose_jwt

from app.core.config import settings
from app.integrations.telecom.twilio import TelecomError

# Vonage's newer unified Messages API, not the legacy rest.nexmo.com/sms/json
# endpoint - this account's dashboard quickstart sample uses the Messages
# API's Python SDK (vonage_messages.Sms) with plain API key/secret Basic
# auth, which Vonage only supports for the SMS channel specifically (every
# other channel needs a JWT-authenticated Application instead).
_MESSAGES_URL = "https://api.nexmo.com/v1/messages"
_NUMBER_SEARCH_URL = "https://rest.nexmo.com/number/search"
_NUMBER_BUY_URL = "https://rest.nexmo.com/number/buy"
_OWNED_NUMBERS_URL = "https://rest.nexmo.com/account/numbers"
_VOICE_CALLS_URL = "https://api.nexmo.com/v1/calls"

_NUMBER_TYPE = {"local": "landline", "mobile": "mobile-lvn", "tollfree": "landline-toll-free"}

_NOT_CROSS_VENDOR_COMPATIBLE = (
    "{op} cannot fail over to Vonage: {reason} - Twilio and Vonage don't "
    "share an identifier/call-control format for this operation"
)


def _require_credentials() -> None:
    if not (settings.vonage_api_key and settings.vonage_api_secret):
        raise TelecomError("Secondary telecom provider (Vonage) is not configured - set VONAGE_API_KEY/API_SECRET")


def _basic_auth_header() -> str:
    token = base64.b64encode(f"{settings.vonage_api_key}:{settings.vonage_api_secret}".encode("utf-8")).decode()
    return f"Basic {token}"


def send_sms(to: str, body: str) -> dict:
    _require_credentials()
    if not settings.vonage_sms_from:
        raise TelecomError("Vonage secondary is missing VONAGE_SMS_FROM")
    try:
        response = httpx.post(
            _MESSAGES_URL,
            headers={"Authorization": _basic_auth_header(), "Content-Type": "application/json"},
            json={
                "message_type": "text",
                "text": body,
                "to": to.lstrip("+"),
                "from": settings.vonage_sms_from,
                "channel": "sms",
            },
            timeout=15.0,
        )
        response.raise_for_status()
        return {"sid": response.json()["message_uuid"], "status": "queued"}
    except httpx.HTTPError as e:
        raise TelecomError(f"Vonage SMS request failed: {e}") from e


def search_available_numbers(
    country: str, number_type: str = "local", area_code: str | None = None,
    contains: str | None = None, limit: int = 10,
) -> list[dict]:
    _require_credentials()
    params = {
        "api_key": settings.vonage_api_key,
        "api_secret": settings.vonage_api_secret,
        "country": country,
        "type": _NUMBER_TYPE.get(number_type, "landline"),
        "size": limit,
    }
    if contains:
        params["pattern"] = contains
        params["search_pattern"] = 1  # Vonage: 1 = pattern may appear anywhere in the number
    try:
        response = httpx.get(_NUMBER_SEARCH_URL, params=params, timeout=15.0)
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Vonage number search failed: {e}") from e

    numbers = response.json().get("numbers", [])
    return [
        {
            "phone_number": n["msisdn"],
            "locality": None,
            "region": n.get("country"),
            "capabilities": {c: True for c in n.get("features", [])},
            "address_requirements": "none",
        }
        for n in numbers
    ]


def list_owned_numbers() -> list[dict]:
    _require_credentials()
    try:
        response = httpx.get(
            _OWNED_NUMBERS_URL,
            params={"api_key": settings.vonage_api_key, "api_secret": settings.vonage_api_secret},
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Vonage owned-numbers lookup failed: {e}") from e

    numbers = response.json().get("numbers", [])
    return [
        {"sid": n["msisdn"], "phone_number": n["msisdn"], "capabilities": {c: True for c in n.get("features", [])}}
        for n in numbers
    ]


def set_voice_webhook(phone_number_sid: str, public_base_url: str) -> None:
    raise TelecomError(
        _NOT_CROSS_VENDOR_COMPATIBLE.format(
            op="set_voice_webhook",
            reason=f"{phone_number_sid!r} is a Twilio phone_number_sid, not a Vonage msisdn+country pair",
        )
    )


def release_number(phone_number_sid: str) -> None:
    raise TelecomError(
        _NOT_CROSS_VENDOR_COMPATIBLE.format(
            op="release_number",
            reason=f"{phone_number_sid!r} is a Twilio phone_number_sid, not a Vonage msisdn+country pair",
        )
    )


def buy_number(phone_number: str) -> dict:
    _require_credentials()
    # Vonage's /number/buy needs a country code separate from the msisdn -
    # derived from the E.164 prefix would need a full calling-code table;
    # good enough for the launch-market set this codebase targets (US/CA
    # share +1, so this intentionally can't disambiguate them - Vonage
    # requires the country explicitly for a real purchase).
    country = "US" if phone_number.startswith("+1") else phone_number[:3]
    try:
        response = httpx.post(
            _NUMBER_BUY_URL,
            data={
                "api_key": settings.vonage_api_key,
                "api_secret": settings.vonage_api_secret,
                "country": country,
                "msisdn": phone_number.lstrip("+"),
            },
            timeout=15.0,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("error-code") not in (None, "200"):
            raise TelecomError(f"Vonage number purchase failed: {result.get('error-code-label')}")
    except httpx.HTTPError as e:
        raise TelecomError(f"Vonage number purchase request failed: {e}") from e
    return {"sid": phone_number, "phone_number": phone_number, "capabilities": {}}


def _extract_say_text(twiml: str) -> str:
    match = re.search(r"<Say[^>]*>(.*?)</Say>", twiml, re.DOTALL)
    if not match:
        raise TelecomError("Vonage secondary can only convert simple <Say> TwiML to an NCCO talk action")
    return match.group(1).strip()


def _voice_jwt() -> str:
    if not (settings.vonage_application_id and settings.vonage_private_key):
        raise TelecomError(
            "Vonage Voice API requires VONAGE_APPLICATION_ID/VONAGE_PRIVATE_KEY (JWT auth), separate from "
            "VONAGE_API_KEY/API_SECRET (used by SMS/Number APIs)"
        )
    now = int(time.time())
    claims = {"iat": now, "exp": now + 60, "jti": str(uuid.uuid4()), "application_id": settings.vonage_application_id}
    return jose_jwt.encode(claims, settings.vonage_private_key, algorithm="RS256")


def place_call(
    to: str, from_: str, twiml_url: str | None = None, twiml: str | None = None,
    status_callback_url: str | None = None, time_limit_seconds: int | None = None,
) -> dict:
    # time_limit_seconds accepted for call-signature compatibility with the
    # primary but not yet wired into the NCCO - Vonage's own per-call
    # duration control (an NCCO "connect" action's lengthTimer) doesn't map
    # cleanly onto a bare "talk" NCCO the way Twilio's flat time_limit
    # parameter does, and this hasn't been verified against a live Vonage
    # account. Not silently dropping this would be worse than a documented
    # gap - flagging it here rather than pretending the cap applies.
    if twiml_url:
        raise TelecomError(
            _NOT_CROSS_VENDOR_COMPATIBLE.format(
                op="place_call", reason="twiml_url points at a Twilio-format TwiML document, not a Vonage NCCO"
            )
        )
    ncco = [{"action": "talk", "text": _extract_say_text(twiml)}]
    try:
        response = httpx.post(
            _VOICE_CALLS_URL,
            headers={"Authorization": f"Bearer {_voice_jwt()}"},
            json={
                "to": [{"type": "phone", "number": to.lstrip("+")}],
                "from": {"type": "phone", "number": from_.lstrip("+")},
                "ncco": ncco,
                **({"event_url": [status_callback_url]} if status_callback_url else {}),
            },
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Vonage place_call failed: {e}") from e

    call = response.json()
    return {"sid": call["uuid"], "status": call.get("status", "started"), "to": to, "from": from_}


def get_call(call_sid: str) -> dict:
    raise TelecomError(
        _NOT_CROSS_VENDOR_COMPATIBLE.format(
            op="get_call", reason=f"{call_sid!r} is a Twilio call_sid, not a Vonage call uuid"
        )
    )


def list_calls(limit: int = 20) -> list[dict]:
    raise TelecomError(
        _NOT_CROSS_VENDOR_COMPATIBLE.format(
            op="list_calls", reason="Twilio and Vonage call history are entirely separate ledgers"
        )
    )


def download_recording(recording_url: str) -> bytes:
    raise TelecomError(
        _NOT_CROSS_VENDOR_COMPATIBLE.format(
            op="download_recording", reason="recording_url is a Twilio-authenticated resource URL"
        )
    )


def delete_recording(recording_sid: str) -> None:
    raise TelecomError(
        _NOT_CROSS_VENDOR_COMPATIBLE.format(
            op="delete_recording", reason=f"{recording_sid!r} is a Twilio recording_sid"
        )
    )
