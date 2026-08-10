"""
Provider Gateway for Telnyx (telecom category) - Architecture doc Phase 4
"direct carrier integrations". Telnyx owns its own global IP network
(unlike Twilio/Vonage, which are CPaaS layers reselling underlying
carriers), so this is the module the roadmap doc's "margin can be
reclaimed later through direct carrier relationships" note is about.

Deliberately standalone, not wired into twilio.py's telecom_failover_enabled
dispatch chain - that chain is Vonage's slot specifically (a same-shape
emergency fallback when Twilio is down). This module is a future
alternative PRIMARY for a cost/ownership decision that hasn't been made yet
(e.g. "route new US number purchases through Telnyx instead of Twilio").
No call site in the app imports this yet; it exists so the switch is ready
to make once TELNYX_ENABLED + real credentials land, per this codebase's
established pattern for provider modules built ahead of the business
decision to use them (same as every *_secondary_stub.py file).

NOT tested against a live account - no real Telnyx credentials exist yet.
Endpoint shapes below were verified against Telnyx's current API v2
documentation (developers.telnyx.com) at the time this was written, not
assumed from memory.

Telnyx's Call Control API is fundamentally webhook/event-driven (a "dial"
command returns a call_control_id; call state changes arrive as webhook
events, there is no simple synchronous "fetch current call status" REST
resource the way Twilio's CallResource works) - get_call/list_calls raise a
clearly-labeled TelecomError explaining this rather than pretending
Twilio-shaped parity that doesn't exist, same "no silent failure" principle
_secondary_stub.py already applies to its own cross-vendor gaps.
"""

import base64

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.config import settings
from app.integrations.telecom.twilio import TelecomError
from app.observability.service import trace_provider_call

_BASE_URL = "https://api.telnyx.com/v2"

_NUMBER_TYPE = {"local": "local", "mobile": "mobile", "tollfree": "toll_free"}


def _require_credentials() -> None:
    if not settings.telnyx_api_key:
        raise TelecomError("Direct carrier (Telnyx) is not configured - set TELNYX_API_KEY")


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.telnyx_api_key}", "Content-Type": "application/json"}


def health_check() -> dict:
    """Presence-only, same rationale as the other Provider Gateways in this
    codebase: the send/search paths are exercised for real on first
    genuine use, and a synthetic call here would just cost against the
    account for no operational benefit."""
    if not settings.telnyx_enabled:
        return {"configured": False, "ok": False, "detail": "TELNYX_ENABLED is false"}
    if not settings.telnyx_api_key:
        return {"configured": False, "ok": False, "detail": None}
    return {"configured": True, "ok": True, "detail": None}


def send_sms(to: str, body: str) -> dict:
    _require_credentials()
    if not settings.telnyx_messaging_profile_id:
        raise TelecomError("Telnyx is missing TELNYX_MESSAGING_PROFILE_ID")
    try:
        with trace_provider_call("telnyx", "send_sms"):
            response = httpx.post(
                f"{_BASE_URL}/messages",
                headers=_headers(),
                json={"messaging_profile_id": settings.telnyx_messaging_profile_id, "to": to, "text": body},
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx SMS request failed: {e}") from e

    data = response.json()["data"]
    return {"sid": data["id"], "status": data.get("to", [{}])[0].get("status", "queued")}


def search_available_numbers(
    country: str, number_type: str = "local", area_code: str | None = None,
    contains: str | None = None, limit: int = 10,
) -> list[dict]:
    _require_credentials()
    params = {
        "filter[country_code]": country,
        "filter[phone_number_type]": _NUMBER_TYPE.get(number_type, "local"),
        "filter[limit]": limit,
    }
    if area_code:
        params["filter[national_destination_code]"] = area_code
    if contains:
        params["filter[phone_number][contains]"] = contains

    try:
        with trace_provider_call("telnyx", "search_available_numbers"):
            response = httpx.get(f"{_BASE_URL}/available_phone_numbers", headers=_headers(), params=params, timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx number search failed: {e}") from e

    numbers = response.json().get("data", [])
    return [
        {
            "phone_number": n["phone_number"],
            "locality": (n.get("region_information") or [{}])[0].get("region_name"),
            "region": (n.get("region_information") or [{}])[0].get("region_type"),
            "capabilities": {cap: True for cap in n.get("features", [])},
            "address_requirements": n.get("cost_information", {}).get("upfront_cost", "none"),
        }
        for n in numbers
    ]


def list_owned_numbers() -> list[dict]:
    _require_credentials()
    try:
        with trace_provider_call("telnyx", "list_owned_numbers"):
            response = httpx.get(f"{_BASE_URL}/phone_numbers", headers=_headers(), timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx owned-numbers lookup failed: {e}") from e

    numbers = response.json().get("data", [])
    return [
        {"sid": n["id"], "phone_number": n["phone_number"], "capabilities": {f: True for f in n.get("features", [])}}
        for n in numbers
    ]


def buy_number(phone_number: str) -> dict:
    """Telnyx purchases go through a Number Order resource rather than a
    single-call create-and-own like Twilio's IncomingPhoneNumbers - the
    order is submitted, and (for numbers not requiring manual carrier
    review) is typically fulfilled within seconds. Assigns the connection
    at order time so the number is immediately reachable through our Call
    Control Application, mirroring buy_number()'s voice_url wiring in
    twilio.py."""
    _require_credentials()
    if not settings.telnyx_connection_id:
        raise TelecomError("Telnyx is missing TELNYX_CONNECTION_ID")

    try:
        with trace_provider_call("telnyx", "buy_number"):
            response = httpx.post(
                f"{_BASE_URL}/number_orders",
                headers=_headers(),
                json={
                    "phone_numbers": [{"phone_number": phone_number}],
                    "connection_id": settings.telnyx_connection_id,
                    **(
                        {"messaging_profile_id": settings.telnyx_messaging_profile_id}
                        if settings.telnyx_messaging_profile_id else {}
                    ),
                },
                timeout=20.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx number purchase failed: {e}") from e

    order = response.json()["data"]
    ordered_number = order["phone_numbers"][0]
    return {
        "sid": ordered_number.get("id", ordered_number.get("phone_number")),
        "phone_number": ordered_number["phone_number"],
        "capabilities": {},
    }


def set_voice_webhook(phone_number_id: str, public_base_url: str) -> None:
    """Telnyx doesn't take a webhook URL per number the way Twilio does -
    inbound events for a number go wherever its assigned Call Control
    Application's webhook_event_url points, configured once on the
    connection (portal, or PATCH /v2/call_control_applications/{id}), not
    per number. Re-pointing a number here just (re)assigns it to our
    configured connection; changing where that connection's OWN webhook
    points is a separate, one-time setup step outside this per-number
    call, which is why public_base_url isn't sent anywhere below - it's
    accepted only to keep this function's signature interchangeable with
    twilio.py's version for callers that don't care which vendor they're
    talking to."""
    _require_credentials()
    if not settings.telnyx_connection_id:
        raise TelecomError("Telnyx is missing TELNYX_CONNECTION_ID")
    try:
        with trace_provider_call("telnyx", "set_voice_webhook"):
            response = httpx.patch(
                f"{_BASE_URL}/phone_numbers/{phone_number_id}",
                headers=_headers(),
                json={"connection_id": settings.telnyx_connection_id},
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx connection assignment failed: {e}") from e


def release_number(phone_number_id: str) -> None:
    _require_credentials()
    try:
        with trace_provider_call("telnyx", "release_number"):
            response = httpx.delete(f"{_BASE_URL}/phone_numbers/{phone_number_id}", headers=_headers(), timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx number release failed: {e}") from e


def place_call(
    to: str, from_: str, twiml_url: str | None = None, twiml: str | None = None,
    status_callback_url: str | None = None,
) -> dict:
    """Telnyx's Call Control has no TwiML equivalent - `twiml`/`twiml_url`
    are rejected outright rather than silently ignored, since accepting
    them without honoring the instructions they encode would be a much
    worse failure mode than refusing up front. Real call flows on Telnyx
    are driven by webhook events + Call Control commands (answer, speak,
    playback, hangup, ...), which would need to be built out as their own
    feature once this carrier is actually adopted - out of scope for this
    groundwork module."""
    _require_credentials()
    if not settings.telnyx_connection_id:
        raise TelecomError("Telnyx is missing TELNYX_CONNECTION_ID")
    if twiml or twiml_url:
        raise TelecomError(
            "Telnyx has no TwiML equivalent - Call Control uses webhook-driven commands instead, "
            "which aren't implemented in this groundwork module"
        )

    try:
        with trace_provider_call("telnyx", "place_call"):
            response = httpx.post(
                f"{_BASE_URL}/calls",
                headers=_headers(),
                json={
                    "connection_id": settings.telnyx_connection_id,
                    "to": to,
                    "from": from_,
                    **({"webhook_url": status_callback_url} if status_callback_url else {}),
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx place_call failed: {e}") from e

    data = response.json()["data"]
    return {"sid": data["call_control_id"], "status": "initiated", "to": to, "from": from_}


def get_call(call_sid: str) -> dict:
    raise TelecomError(
        "Telnyx Call Control has no synchronous call-status fetch - state changes arrive as webhook "
        "events, not a pollable call resource"
    )


def list_calls(limit: int = 20) -> list[dict]:
    raise TelecomError(
        "Telnyx Call Control has no call-history listing endpoint analogous to Twilio's - "
        "call detail records are delivered via webhook events or the separate CDR export"
    )


def download_recording(recording_url: str) -> bytes:
    """Unlike Vonage, Telnyx's Recordings API is real and REST-fetchable -
    recording_url here is expected to be the download_urls.mp3/wav value
    from a recording.saved webhook event or GET /v2/recordings, which is
    already a pre-signed, directly-fetchable URL (no additional auth
    header needed, unlike Twilio's Basic-Auth-protected media URLs)."""
    try:
        with trace_provider_call("telnyx", "download_recording"):
            response = httpx.get(recording_url, timeout=30.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Could not download Telnyx recording: {e}") from e
    return response.content


def delete_recording(recording_id: str) -> None:
    _require_credentials()
    try:
        with trace_provider_call("telnyx", "delete_recording"):
            response = httpx.delete(f"{_BASE_URL}/recordings/{recording_id}", headers=_headers(), timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Telnyx recording deletion failed: {e}") from e


def validate_webhook_signature(payload: bytes, signature: str | None, timestamp: str | None) -> bool:
    """Telnyx signs webhooks with Ed25519 (asymmetric), not Twilio's
    shared-secret HMAC - verifies "{timestamp}|{payload}" against the
    telnyx-signature-ed25519 header using Telnyx's published public key
    (TELNYX_PUBLIC_KEY), not a secret this app holds. False (not an
    exception) whenever verification can't proceed, matching
    twilio.validate_webhook_signature's "no signature -> False" contract
    so callers handle both vendors identically."""
    if not signature or not timestamp or not settings.telnyx_public_key:
        return False
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(settings.telnyx_public_key))
        signed_payload = timestamp.encode("utf-8") + b"|" + payload
        public_key.verify(base64.b64decode(signature), signed_payload)
        return True
    except (InvalidSignature, ValueError):
        return False
