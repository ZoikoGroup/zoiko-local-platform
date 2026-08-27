"""
Provider Gateway for Twilio (telecom category). Per CLAUDE.md's Provider Gateway
rule, this is the ONLY file allowed to import the `twilio` SDK directly —
everything else in the app calls the functions below instead.

Groundwork built ahead of Stage 1 finishing (see CLAUDE.md's 2026-07-30
exception note). No Account/Number model linkage, no audit logging, no
entitlement checks — that gets added once Stage 1/2 land properly.
"""

import json
import re

import httpx
from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant
from twilio.request_validator import RequestValidator
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover
from app.observability.service import trace_provider_call

_NUMBER_TYPE_PATH = {"local": "Local", "mobile": "Mobile", "tollfree": "TollFree"}

_breaker = CircuitBreaker("telecom")


def circuit_state() -> str:
    return _breaker.state.value


class TelecomError(Exception):
    """Raised instead of letting TwilioRestException escape this module —
    callers elsewhere in the app should never need to know or catch a
    vendor-specific exception type (Provider Gateway rule)."""


def _clean_twilio_error_message(e: TwilioException) -> str:
    """Confirmed live: a Twilio trial-account restriction on
    AvailablePhoneNumbers reached the customer as the literal raw Python
    exception dump - "('Unable to fetch page', HTTP 401
    {"code":20003,"message":"This feature is not available on a Trial
    account...","more_info":"...","status":401})" - because the SDK
    raises the bare TwilioException base class (not the TwilioRestException
    subclass with a clean parsed .msg) for failures during a paginated
    list fetch. Extracts Twilio's own "message" field from the embedded
    JSON where possible, instead of leaking that raw dump to the frontend."""
    if isinstance(e, TwilioRestException) and e.msg:
        message = e.msg
    else:
        raw = str(e)
        match = re.search(r"\{.*\}", raw)
        message = raw
        if match:
            try:
                extracted = json.loads(match.group(0)).get("message")
                if extracted:
                    message = extracted
            except (json.JSONDecodeError, AttributeError):
                pass

    # Confirmed live (2026-08-18): the extracted message IS clean at this
    # point (no raw dump), but it's still written from Twilio's own
    # perspective, aimed at whoever holds the Twilio account (us) - "This
    # feature is not available on a Trial account. Please upgrade your
    # account to gain access." A customer reading that reasonably assumes
    # THEIR Zoiko subscription needs upgrading, not that our own Twilio
    # account is trial-restricted. Same customer-safe-translation posture
    # as the raw-dump fix above, just for a message that's clean but
    # wrong-audience rather than unparsed.
    if "trial account" in message.lower():
        return "Number search/purchase is temporarily unavailable while we finish setting up this feature. Please try again shortly."
    return message


def _is_provider_failure(e: Exception) -> bool:
    """Passed as with_failover's is_breaker_failure - _breaker is a single
    process-wide instance shared by every telecom operation (calls, SMS,
    number lookups, ...) for every account on the platform, so what counts
    as a "failure" here matters beyond just this one request. Every
    TelecomError raised in this module wraps the original TwilioException
    via `from e`, so e.__cause__ is that original exception.

    A TwilioRestException carries the real HTTP status Twilio returned. A
    4xx means Twilio understood and rejected THIS specific request (bad
    destination, unsupported region, invalid number, permission not
    enabled for a country, ...) - an expected, per-request outcome that
    says nothing about whether Twilio itself is healthy. Confirmed live:
    three back-to-back 400s from repeatedly retrying an unsupported SMS
    destination tripped this breaker OPEN, which then blocked SMS/calls
    for every other account on the platform for the next 30 seconds, even
    though Twilio was never actually down. Only a 5xx (or no status at
    all - a connection/timeout-level failure with nothing HTTP to inspect)
    should count as a real provider-health signal."""
    status = getattr(getattr(e, "__cause__", None), "status", None)
    return status is None or status >= 500


# Imported after TelecomError is defined - _secondary_stub imports it back
# from this module, which would otherwise be a circular import.
from app.integrations.telecom import _secondary_stub as secondary  # noqa: E402


def _client() -> Client:
    # An API Key (SKxxx + secret) authenticates as itself, so the account SID
    # must be passed separately - unlike the master auth token, where the
    # account SID doubles as the username. See twilio_api_key_sid's docstring
    # in core/config.py for why this is preferred when set.
    if settings.twilio_api_key_sid and settings.twilio_api_key_secret:
        return Client(settings.twilio_api_key_sid, settings.twilio_api_key_secret, settings.twilio_account_sid)
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

    def _primary() -> dict:
        # Deliberately raised from inside _primary (not before with_failover
        # is called) - a missing TWILIO_TRIAL_NUMBER is exactly the kind of
        # primary-provider failure the secondary should get a chance to
        # rescue. Raising it earlier skipped with_failover entirely, silently
        # defeating TELECOM_FAILOVER_ENABLED for this one function.
        if not settings.twilio_trial_number:
            raise TelecomError("No Twilio notification number configured (TWILIO_TRIAL_NUMBER)")
        try:
            with trace_provider_call("twilio", "send_sms"):
                message = _client().messages.create(to=to, from_=settings.twilio_trial_number, body=body)
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return {"sid": message.sid, "status": message.status}

    secondary_fn = (lambda: secondary.send_sms(to, body)) if settings.telecom_failover_enabled else None
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def send_whatsapp_message(to: str, from_number: str, body: str) -> dict:
    """Phase 3 "WhatsApp Business integration" - `from_number` is a
    customer's own PSTN number that has completed WhatsApp Business sender
    approval out-of-band (see PhoneNumber.whatsapp_enabled); Twilio
    addresses WhatsApp participants with a `whatsapp:` scheme on both
    sides of the same Messages API `send_sms` already uses. No secondary
    provider yet - a WhatsApp-specific failover path doesn't exist in this
    codebase, so this only gets circuit-breaker protection, not failover.
    """
    def _primary() -> dict:
        try:
            with trace_provider_call("twilio", "send_whatsapp_message"):
                message = _client().messages.create(
                    to=f"whatsapp:{to}", from_=f"whatsapp:{from_number}", body=body
                )
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return {"sid": message.sid, "status": message.status}

    return with_failover(_breaker, _primary, None, TelecomError, _is_provider_failure)


def send_customer_sms(to: str, from_number: str, body: str) -> dict:
    """Phase 3 "SMS by regulated market" - unlike send_sms() above (a fixed
    Zoiko-owned notification number), `from_number` here is a customer's
    own PSTN number that has completed A2P 10DLC brand/campaign
    registration out-of-band (see PhoneNumber.sms_enabled). No secondary
    provider yet, same posture as send_whatsapp_message.
    """
    def _primary() -> dict:
        try:
            with trace_provider_call("twilio", "send_customer_sms"):
                message = _client().messages.create(to=to, from_=from_number, body=body)
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return {"sid": message.sid, "status": message.status}

    return with_failover(_breaker, _primary, None, TelecomError, _is_provider_failure)


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

    def _primary() -> list[dict]:
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
            raise TelecomError(_clean_twilio_error_message(e)) from e

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

    secondary_fn = (
        (lambda: secondary.search_available_numbers(country, number_type, area_code, contains, limit))
        if settings.telecom_failover_enabled else None
    )
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def list_owned_numbers() -> list[dict]:
    def _primary() -> list[dict]:
        try:
            with trace_provider_call("twilio", "list_owned_numbers"):
                numbers = _client().incoming_phone_numbers.list()
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return [{"sid": n.sid, "phone_number": n.phone_number, "capabilities": n.capabilities} for n in numbers]

    secondary_fn = secondary.list_owned_numbers if settings.telecom_failover_enabled else None
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def set_voice_webhook(phone_number_sid: str, public_base_url: str) -> None:
    """(Re)points an already-purchased number's voice webhook + status
    callback, AND its SMS webhook, at the given base URL - needed whenever
    PUBLIC_BASE_URL changes (e.g. a new ngrok tunnel in dev), since
    buy_number() only sets these at purchase time. Despite the name (kept
    for backward compatibility with its existing caller), this also covers
    SMS - without sms_url, Twilio has nowhere to POST an inbound text and
    /messaging/sms/incoming (a real, working route) never gets called.
    """
    def _primary() -> None:
        try:
            with trace_provider_call("twilio", "set_voice_webhook"):
                _client().incoming_phone_numbers(phone_number_sid).update(
                    voice_url=f"{public_base_url}/media/voice/incoming",
                    voice_method="POST",
                    status_callback=f"{public_base_url}/media/voice/status-callback",
                    status_callback_method="POST",
                    sms_url=f"{public_base_url}/messaging/sms/incoming",
                    sms_method="POST",
                )
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e

    secondary_fn = (
        (lambda: secondary.set_voice_webhook(phone_number_sid, public_base_url))
        if settings.telecom_failover_enabled else None
    )
    with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def release_number(phone_number_sid: str) -> None:
    """Actually releases a purchased number back to Twilio - without this,
    cancelling a number in our own DB leaves it sitting active (and billing)
    on the real Twilio account forever."""
    def _primary() -> None:
        try:
            with trace_provider_call("twilio", "release_number"):
                _client().incoming_phone_numbers(phone_number_sid).delete()
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e

    secondary_fn = (lambda: secondary.release_number(phone_number_sid)) if settings.telecom_failover_enabled else None
    with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def buy_number(phone_number: str, *, bundle_sid: str | None = None) -> dict:
    """Written directly against the documented IncomingPhoneNumbers create
    contract. Confirmed live against a real Twilio trial account.

    Registers our own voice webhook (the URL Twilio actually calls when
    someone dials this number - without it, a purchased number never reaches
    /media/voice/incoming at all), a status-callback URL for the final
    completed/duration event, and an SMS webhook (so a text sent to this
    number reaches /messaging/sms/incoming instead of going nowhere - that
    route already exists and works, it just needs Twilio told to call it),
    all only when a public base URL is configured (nothing to point at
    otherwise, e.g. before ngrok is running in dev).

    bundle_sid: a Twilio-approved Regulatory Bundle (see
    get_bundle_status/submit_bundle_for_review below) - required by Twilio
    itself for restricted number types in countries like the UK; omitted
    entirely for number types/countries that don't need one (e.g. US)."""
    kwargs = {"phone_number": phone_number}
    if bundle_sid:
        kwargs["bundle_sid"] = bundle_sid
    if settings.public_base_url:
        kwargs["voice_url"] = f"{settings.public_base_url}/media/voice/incoming"
        kwargs["voice_method"] = "POST"
        kwargs["status_callback"] = f"{settings.public_base_url}/media/voice/status-callback"
        kwargs["status_callback_method"] = "POST"
        kwargs["sms_url"] = f"{settings.public_base_url}/messaging/sms/incoming"
        kwargs["sms_method"] = "POST"

    def _primary() -> dict:
        try:
            with trace_provider_call("twilio", "buy_number"):
                number = _client().incoming_phone_numbers.create(**kwargs)
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return {"sid": number.sid, "phone_number": number.phone_number, "capabilities": number.capabilities}

    # Vonage failover deliberately skipped here (2026-08-21) - confirmed live
    # that this account's Vonage credentials authenticate fine everywhere
    # else (balance check, search) but get a flat 401 specifically on
    # /number/buy, so falling back here only adds a slow, guaranteed-to-fail
    # second attempt before the customer sees an error. Every other telecom
    # operation (search, calls, SMS) still fails over normally - remove this
    # override once Vonage's account is confirmed able to purchase numbers.
    return with_failover(_breaker, _primary, None, TelecomError, _is_provider_failure)


# --- Regulatory Compliance (real Twilio-reviewed identity bundles required
# for restricted number types/countries, e.g. UK local numbers) ---
# No secondary/failover provider for any of these - Vonage has no
# equivalent concept, an architectural gap like buy_number's own Vonage
# skip above, not a misconfiguration.

def get_regulation_requirements(iso_country: str, number_type: str, end_user_type: str) -> list[dict]:
    """What Twilio actually requires before it'll approve a bundle for this
    country/number_type/end_user_type - confirmed live (2026-08-22) this is
    real, per-country data (UK individuals need a government ID or passport
    plus name/email/phone; other countries/types differ), not something to
    hardcode per country in our own code."""
    try:
        with trace_provider_call("twilio", "get_regulation_requirements"):
            regulations = _client().numbers.v2.regulatory_compliance.regulations.list(
                iso_country=iso_country, number_type=number_type, end_user_type=end_user_type, limit=5,
            )
    except TwilioException as e:
        raise TelecomError(_clean_twilio_error_message(e)) from e
    return [r.requirements for r in regulations]


def create_regulatory_end_user(*, friendly_name: str, end_user_type: str, attributes: dict) -> dict:
    try:
        with trace_provider_call("twilio", "create_regulatory_end_user"):
            end_user = _client().numbers.v2.regulatory_compliance.end_users.create(
                friendly_name=friendly_name, type=end_user_type, attributes=attributes,
            )
    except TwilioException as e:
        raise TelecomError(_clean_twilio_error_message(e)) from e
    return {"sid": end_user.sid, "type": end_user.type}


def upload_supporting_document(
    *, friendly_name: str, document_type: str, attributes: dict, file_bytes: bytes, content_type: str
) -> dict:
    """The Python SDK's SupportingDocuments.create() only sends JSON
    (friendly_name/type/attributes) - it has no parameter for the actual ID
    scan. Twilio's real REST API accepts the file as a multipart part
    alongside those same fields, so this calls the HTTP API directly
    (same escape hatch buy_number's own module docstring implies is fine
    when the SDK doesn't cover something) rather than force-fitting it
    through the SDK's incomplete wrapper."""
    if not (settings.twilio_account_sid and settings.twilio_auth_token):
        raise TelecomError("Twilio credentials are not configured")
    try:
        with trace_provider_call("twilio", "upload_supporting_document"):
            response = httpx.post(
                "https://numbers.twilio.com/v2/RegulatoryCompliance/SupportingDocuments",
                auth=(settings.twilio_account_sid, settings.twilio_auth_token),
                data={"FriendlyName": friendly_name, "Type": document_type, "Attributes": json.dumps(attributes)},
                files={"File": ("document", file_bytes, content_type)},
                timeout=30.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise TelecomError(f"Twilio supporting document upload failed: {e}") from e
    result = response.json()
    return {"sid": result["sid"], "type": result["type"]}


def create_regulatory_bundle(
    *, friendly_name: str, email: str, iso_country: str, end_user_type: str, number_type: str,
    status_callback: str | None = None,
) -> dict:
    """status_callback: when set, Twilio POSTs BundleSid/Status/FailureReason
    to this URL the moment its own review team approves or rejects the
    bundle - a real-time push instead of waiting for the customer to click
    "check status" or for the daily reconciliation sweep to notice. Passed
    only when a public URL is actually configured (same conditional pattern
    as buy_number's voice_url above) - there's nothing to point Twilio at
    otherwise, e.g. before ngrok is running in dev."""
    kwargs = {
        "friendly_name": friendly_name, "email": email, "iso_country": iso_country,
        "end_user_type": end_user_type, "number_type": number_type,
    }
    if status_callback:
        kwargs["status_callback"] = status_callback
    try:
        with trace_provider_call("twilio", "create_regulatory_bundle"):
            bundle = _client().numbers.v2.regulatory_compliance.bundles.create(**kwargs)
    except TwilioException as e:
        raise TelecomError(_clean_twilio_error_message(e)) from e
    return {"sid": bundle.sid, "status": bundle.status}


def create_bundle_item_assignment(bundle_sid: str, object_sid: str) -> dict:
    try:
        with trace_provider_call("twilio", "create_bundle_item_assignment"):
            assignment = _client().numbers.v2.regulatory_compliance.bundles(bundle_sid).item_assignments.create(
                object_sid=object_sid,
            )
    except TwilioException as e:
        raise TelecomError(_clean_twilio_error_message(e)) from e
    return {"sid": assignment.sid}


def submit_bundle_for_review(bundle_sid: str) -> dict:
    try:
        with trace_provider_call("twilio", "submit_bundle_for_review"):
            bundle = _client().numbers.v2.regulatory_compliance.bundles(bundle_sid).update(status="pending-review")
    except TwilioException as e:
        raise TelecomError(_clean_twilio_error_message(e)) from e
    return {"sid": bundle.sid, "status": bundle.status}


def get_bundle_status(bundle_sid: str) -> dict:
    try:
        with trace_provider_call("twilio", "get_bundle_status"):
            bundle = _client().numbers.v2.regulatory_compliance.bundles(bundle_sid).fetch()
    except TwilioException as e:
        raise TelecomError(_clean_twilio_error_message(e)) from e
    return {"sid": bundle.sid, "status": bundle.status, "rejection_reason": getattr(bundle, "rejection_reason", None)}


def place_call(
    to: str, from_: str, twiml_url: str | None = None, twiml: str | None = None,
    status_callback_url: str | None = None, time_limit_seconds: int | None = None,
) -> dict:
    """`from_` must be a Twilio number owned on this account. Confirmed live:
    Twilio rejects with a 400 ("not yet verified for your account") if `from_`
    isn't an owned/verified number — see docs/stage3-twilio-calling-notes.md.

    time_limit_seconds maps straight to Twilio's own Calls.create time_limit
    parameter - a real server-side hard cap (Twilio hangs up the call itself
    once reached), not a client-side check. Production Readiness Standard
    Table 15's "Usage ceilings: hard limits on... call duration" - see
    app.risk.service.get_call_time_limit_for_account for who gets a cap.
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
    if time_limit_seconds is not None:
        kwargs["time_limit"] = time_limit_seconds

    def _primary() -> dict:
        try:
            with trace_provider_call("twilio", "place_call"):
                call = _client().calls.create(**kwargs)
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        # CallInstance has no public `from_` attribute (the twilio-python
        # SDK only exposes it via kwargs when creating a call) - the "from"
        # number on a fetched/created instance is the private `_from`.
        return {"sid": call.sid, "status": call.status, "to": call.to, "from": call._from}

    secondary_fn = (
        (lambda: secondary.place_call(to, from_, twiml_url, twiml, status_callback_url, time_limit_seconds))
        if settings.telecom_failover_enabled else None
    )
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def get_call(call_sid: str) -> dict:
    """price/price_unit are Twilio's own real, documented Call resource
    fields (what Twilio actually billed this account for the call) - not an
    estimate this codebase invents. price is a decimal string in major
    currency units (e.g. "-0.03000" = 3 cents, negative because it's a
    debit) and can be None for a while after the call ends, since Twilio
    rates calls asynchronously - see capture_wholesale_call_cost's retry
    posture in app.billing.service for the caller-side handling of that."""
    def _primary() -> dict:
        try:
            with trace_provider_call("twilio", "get_call"):
                call = _client().calls(call_sid).fetch()
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return {
            "sid": call.sid, "status": call.status, "to": call.to, "from": call._from, "duration": call.duration,
            "price": call.price, "price_unit": call.price_unit,
        }

    secondary_fn = (lambda: secondary.get_call(call_sid)) if settings.telecom_failover_enabled else None
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def list_calls(limit: int = 20) -> list[dict]:
    """Read-only, confirmed live-working with zero owned numbers and zero
    calls made (returns an empty list, not an error).
    """
    def _primary() -> list[dict]:
        try:
            with trace_provider_call("twilio", "list_calls"):
                calls = _client().calls.list(limit=limit)
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e
        return [{"sid": c.sid, "status": c.status, "to": c.to, "from": c._from} for c in calls]

    secondary_fn = (lambda: secondary.list_calls(limit)) if settings.telecom_failover_enabled else None
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


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
        # <Dial> itself has no statusCallback/statusCallbackEvent attribute
        # in Twilio's TwiML schema (those exist only on the nested <Number>/
        # <Client>/<Sip> nouns) - action is the real, documented <Dial>
        # attribute Twilio actually calls when the dial completes.
        dial_kwargs["action"] = status_callback_url
    if recording_callback_url:
        dial_kwargs["record"] = "record-from-answer-dual"
        dial_kwargs["recording_status_callback"] = recording_callback_url
        dial_kwargs["recording_status_callback_method"] = "POST"
        dial_kwargs["recording_status_callback_event"] = "completed"
    response.dial(forwarding_number, **dial_kwargs)
    return str(response)


def build_voice_access_token(identity: str) -> str:
    """Short-lived (1hr) Twilio Voice access token for the browser calling
    SDK (@twilio/voice-sdk) - lets the browser register as a real Twilio
    Client and place calls directly, no separate phone involved (unlike
    place_bridge_call's call-bridging, which needs a real phone to ring
    first). `identity` is opaque to Twilio - just a value we choose now
    and read back later - see media.service.handle_browser_connect,
    where it's the account_id, so that webhook can run the exact same
    billing/risk checks as every other real outbound call. Uses a
    dedicated signing key (twilio_voice_api_key_sid/secret), never the
    general-purpose twilio_api_key_sid/secret - different scope entirely
    (client-side Voice grants vs. server-side REST API calls).

    incoming_allow=True so this same identity/token also lets the browser
    receive real inbound calls - build_ring_group_response dials
    "client:<account_id>" alongside the number's configured phone
    destinations on every inbound call, and Twilio only actually delivers
    that leg to a browser tab that's registered a Device with a token
    carrying this grant."""
    token = AccessToken(
        settings.twilio_account_sid, settings.twilio_voice_api_key_sid,
        settings.twilio_voice_api_key_secret, identity=identity, ttl=3600,
    )
    token.add_grant(VoiceGrant(outgoing_application_sid=settings.twilio_twiml_app_sid, incoming_allow=True))
    return token.to_jwt()


def build_bridge_response(destination: str, caller_id: str, status_callback_url: str | None = None) -> str:
    """Builds TwiML that dials `destination` with `caller_id` shown as the
    caller's number, for the second leg of a call-bridge: the platform has
    already called the agent's own real phone and they've answered (that's
    what triggers Twilio to request this response), so this is what
    connects them live to the actual customer. caller_id is set so the
    customer sees the Zoiko Local number, not the agent's personal one.
    """
    response = VoiceResponse()
    dial_kwargs: dict = {"caller_id": caller_id}
    number_kwargs: dict = {}
    if status_callback_url:
        # action belongs on <Dial> itself; status_callback/status_callback_event
        # are only valid on the nested <Number> noun - Twilio's XML validator
        # rejects them on <Dial> (confirmed live via a real call's Notifications:
        # "Attribute 'statusCallback' is not allowed to appear in element
        # 'Dial'" - tolerated as a warning, not fatal, but still real invalid
        # TwiML worth fixing outright).
        dial_kwargs["action"] = status_callback_url
        number_kwargs["status_callback"] = status_callback_url
        number_kwargs["status_callback_event"] = "completed"
    dial = response.dial(**dial_kwargs)
    dial.number(destination, **number_kwargs)
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
    """Shared by two Phase 3/Phase 2 features - the Advanced IVR builder's
    FORWARD node (Phase 3) and enhanced business routing (Architecture doc
    Phase 2) both use this as a superset of build_forward_response: rings
    every destination in `destinations` simultaneously (multiple <Number>
    children under one <Dial> - Twilio's native ring-group primitive, first
    to answer wins, the rest stop ringing) instead of a single number.
    `fallback_action_url` is always set (unlike build_forward_response's
    optional action) - see media/voice.py's /flow-forward-fallback route
    (IVR builder, resolves the node's own on_no_answer_node_id) and
    /forward-fallback route (enhanced business routing, routes to
    voicemail), both of which only fire when the dial genuinely wasn't
    answered.

    A destination prefixed "client:" (e.g. "client:<account_id>", the same
    identity build_voice_access_token issues browser tokens under) rings
    a registered browser tab instead of a phone - see media/voice.py's
    _default_call_twiml and _flow_response, which both prepend this
    destination alongside the number's configured phone destinations.
    Mixed real numbers and browser clients ring simultaneously in the same
    ring group; whichever answers first wins, same as multiple phone
    numbers already do.
    """
    response = VoiceResponse()
    dial_kwargs = {"action": fallback_action_url}
    if recording_callback_url:
        dial_kwargs["record"] = "record-from-answer-dual"
        dial_kwargs["recording_status_callback"] = recording_callback_url
        dial_kwargs["recording_status_callback_method"] = "POST"
        dial_kwargs["recording_status_callback_event"] = "completed"
    dial = response.dial(**dial_kwargs)
    # action belongs on <Dial> itself; status_callback/status_callback_event
    # are only valid on the nested <Number>/<Client> nouns - Twilio's XML
    # validator rejects them on <Dial> (confirmed live via a real call's
    # Notifications log - see build_bridge_response's identical fix).
    noun_kwargs: dict = {}
    if status_callback_url:
        noun_kwargs["status_callback"] = status_callback_url
        noun_kwargs["status_callback_event"] = "completed"
    for destination in destinations:
        if destination.startswith("client:"):
            dial.client(identity=destination.removeprefix("client:"), **noun_kwargs)
        else:
            dial.number(destination, **noun_kwargs)
    return str(response)


def build_ivr_menu_response(greeting: str, action_url: str, no_input_redirect_url: str) -> str:
    """Enhanced business routing (Architecture doc Phase 2) - "press 1 for
    sales, 2 for support." Gathers a single DTMF digit; Twilio requests
    action_url once any digit is pressed (even if the caller hangs up
    mid-menu, an empty Digits value is posted there - see voice.py's
    /ivr-select). If nothing is pressed before the gather times out,
    Twilio does NOT call action_url at all - it falls through to the
    <Redirect> below instead, which returns the number to its normal
    (non-IVR) call-handling behavior."""
    response = VoiceResponse()
    gather = response.gather(input="dtmf", num_digits=1, action=action_url, method="POST", timeout=6)
    gather.say(greeting)
    response.redirect(no_input_redirect_url, method="POST")
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


def build_enqueue_response(queue_name: str, wait_url: str, action_url: str, overflow_inner_xml: str) -> str:
    """Contact-center-lite (Phase 3) - puts the caller in a real Twilio
    Queue (auto-created on first use, no separate REST provisioning step)
    and appends the overflow node's own TwiML verbs right after </Enqueue>
    in the SAME document: that's what lets Twilio's <Leave/> (returned by
    /media/voice/queue/wait once max_wait_seconds is exceeded) fall through
    to the queue node's configured overflow instead of just hanging up.
    `action_url` fires once the call leaves the queue for any OTHER reason
    (bridged+ended, hangup while waiting) - see /media/voice/queue/left.
    `overflow_inner_xml` is raw TwiML *without* its own <Response> wrapper -
    see media/voice.py's _inner_verbs().
    """
    response = VoiceResponse()
    response.enqueue(queue_name, wait_url=wait_url, wait_url_method="POST", action=action_url, method="POST")
    xml = str(response)
    return xml.replace("</Response>", overflow_inner_xml + "</Response>")


def build_hold_response() -> str:
    """waitUrl TwiML played on a loop (Twilio automatically re-requests
    waitUrl once this finishes, for as long as the call remains enqueued) -
    a spoken hold message rather than sourcing actual hold music."""
    response = VoiceResponse()
    response.say("Please hold, you're next in line. We'll be with you shortly.")
    response.pause(length=8)
    return str(response)


def build_leave_response() -> str:
    """Ends the queue wait once max_wait_seconds is exceeded - control
    returns to whatever TwiML follows the original <Enqueue> verb."""
    response = VoiceResponse()
    response.leave()
    return str(response)


def build_dial_queue_response(queue_name: str) -> str:
    """The agent-facing leg's TwiML once they answer - bridges them to
    whichever call is currently oldest in this Twilio queue."""
    response = VoiceResponse()
    response.dial().queue(queue_name)
    return str(response)


def build_dtmf_menu_response(prompt: str, action_url: str, num_digits: int = 1, timeout: int = 5) -> str:
    """Advanced IVR builder (Phase 3 Call Flow Designer) - the touch-tone
    menu primitive. Twilio collects up to `num_digits` keypresses itself and
    POSTs them as `Digits` to `action_url`; a caller who presses nothing
    within `timeout` seconds also hits `action_url`, just with no Digits
    param, which routing.service.resolve_menu_input() treats as a timeout.
    """
    response = VoiceResponse()
    gather = response.gather(
        input="dtmf", action=action_url, method="POST", num_digits=num_digits, timeout=timeout
    )
    gather.say(prompt)
    # If num_digits is never reached and timeout elapses with a Gather still
    # open, Twilio falls through to whatever comes after it - point that at
    # the same action_url so a silent/empty response still reaches the
    # server as a timeout instead of just hanging up.
    response.redirect(action_url, method="POST")
    return str(response)


def build_receptionist_reply_response(
    message: str, forward_to: str | None = None, fallback_action_url: str | None = None,
    recording_callback_url: str | None = None,
) -> str:
    """Closes out the receptionist flow: a spoken reply, then either an
    escalation dial to a human or a hangup.

    fallback_action_url (real gap fix, renamed from status_callback_url):
    this used to double as BOTH the <Dial> action AND statusCallback/
    statusCallbackEvent, the latter two being silent no-ops on <Dial>
    itself in Twilio's TwiML schema (see build_forward_response's identical
    fix) - action is the one that's real. Worse, the caller was pointing
    this at the generic /media/voice/status-callback, which always returns
    204 with no TwiML - fine for that route's OTHER use as a genuine fire-
    and-forget statusCallback (outbound calls placed via place_call), but
    fatal as an action URL: Twilio expects real TwiML back once the dial
    resolves, for ANY outcome (busy/no-answer/failed/completed), and a bare
    204 just ends the call. Confirmed live: a genuinely urgent call
    forwarded to a human who doesn't pick up was silently disconnected -
    no voicemail, no retry, no notification - the opposite of the plain
    forward/ring-group path's dedicated /forward-fallback. Callers now pass
    a dedicated fallback route (media.receptionist.escalation_fallback)
    that inspects DialCallStatus and falls back to voicemail.

    recording_callback_url (real gap fix): an escalated call - the AI
    Receptionist forwarding a HIGH-urgency caller straight to a human - is
    a live two-way conversation with no other capture mechanism of its
    own, unlike the pre-escalation Gather utterance (which the
    ReceptionistCall row already preserves) or a plain voicemail/forward
    call (each already has its own recording path). Without this, the
    single call category with the least room for missing a detail - an
    urgent handoff - was the one call category that was never recorded.
    Same record="record-from-answer-dual" + recording_status_callback
    shape as build_ring_group_response, and the caller wires it through
    the exact same AI_PROCESSING consent gate (should_record_forwarded_
    call) before ever passing a non-None value here."""
    response = VoiceResponse()
    response.say(message)
    if forward_to:
        dial_kwargs = {}
        if fallback_action_url:
            dial_kwargs["action"] = fallback_action_url
        if recording_callback_url:
            dial_kwargs["record"] = "record-from-answer-dual"
            dial_kwargs["recording_status_callback"] = recording_callback_url
            dial_kwargs["recording_status_callback_method"] = "POST"
            dial_kwargs["recording_status_callback_event"] = "completed"
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
    unauthenticated fetches get a 401, so this can't just be a plain GET.
    Used where only the audio bytes matter (e.g. handing them to Whisper
    for transcription) - see get_recording_media below for callers that
    also need to know the real content type (e.g. serving it to a
    browser's <audio> player)."""
    content, _content_type = get_recording_media(recording_url)
    return content


def get_recording_media(recording_url: str) -> tuple[bytes, str]:
    """Same authenticated fetch as download_recording, but also returns
    Twilio's real Content-Type header (its recordings default to
    audio/x-wav) - a browser <audio>/<a> player needs the correct MIME
    type to play the response voice/voicemail.py streams back, not just
    the raw bytes."""
    def _primary() -> tuple[bytes, str]:
        try:
            with trace_provider_call("twilio", "download_recording"):
                response = httpx.get(
                    recording_url, auth=(settings.twilio_account_sid, settings.twilio_auth_token), timeout=30.0
                )
                response.raise_for_status()
        except httpx.HTTPError as e:
            raise TelecomError(f"Could not download recording: {e}") from e
        return response.content, response.headers.get("content-type", "audio/x-wav")

    secondary_fn = (
        (lambda: (secondary.download_recording(recording_url), "audio/x-wav"))
        if settings.telecom_failover_enabled else None
    )
    return with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


def delete_recording(recording_sid: str) -> None:
    """Actually removes a recording from Twilio's storage - used once a
    voicemail/call recording is past its retention window, so the audio
    doesn't just sit there forever after we stop linking to it."""
    def _primary() -> None:
        try:
            with trace_provider_call("twilio", "delete_recording"):
                _client().recordings(recording_sid).delete()
        except TwilioException as e:
            raise TelecomError(_clean_twilio_error_message(e)) from e

    secondary_fn = (
        (lambda: secondary.delete_recording(recording_sid)) if settings.telecom_failover_enabled else None
    )
    with_failover(_breaker, _primary, secondary_fn, TelecomError, _is_provider_failure)


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


def compute_webhook_signature(url: str, params: dict) -> str:
    """Computes the X-Twilio-Signature our own configured auth token would
    produce for a given URL/params pair - the inverse of
    validate_webhook_signature, used by synthetic self-checks to round-trip
    a signature through the real validator without a live Twilio request."""
    validator = RequestValidator(settings.twilio_auth_token)
    return validator.compute_signature(url, params)
