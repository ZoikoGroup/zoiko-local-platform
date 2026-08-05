"""Stand-in for a second telecom vendor (e.g. Vonage/Plivo) behind the
telecom_failover_enabled flag. No real second-vendor account exists yet -
every function here raises a clearly-labeled error instead of silently
no-opping, so a misconfigured failover fails loudly rather than pretending
to have sent an SMS or placed a call. Swap this module's bodies for a real
SDK client once real secondary credentials are available - callers never
change, since twilio.py dispatches to this module by function name only.
"""

from app.integrations.telecom.twilio import TelecomError

_NOT_CONFIGURED = (
    "secondary telecom provider not configured - set TELECOM_SECONDARY_* "
    "credentials once a second vendor account exists"
)


def send_sms(to: str, body: str) -> dict:
    raise TelecomError(_NOT_CONFIGURED)


def search_available_numbers(
    country: str, number_type: str = "local", area_code: str | None = None,
    contains: str | None = None, limit: int = 10,
) -> list[dict]:
    raise TelecomError(_NOT_CONFIGURED)


def list_owned_numbers() -> list[dict]:
    raise TelecomError(_NOT_CONFIGURED)


def set_voice_webhook(phone_number_sid: str, public_base_url: str) -> None:
    raise TelecomError(_NOT_CONFIGURED)


def release_number(phone_number_sid: str) -> None:
    raise TelecomError(_NOT_CONFIGURED)


def buy_number(phone_number: str) -> dict:
    raise TelecomError(_NOT_CONFIGURED)


def place_call(
    to: str, from_: str, twiml_url: str | None = None, twiml: str | None = None,
    status_callback_url: str | None = None,
) -> dict:
    raise TelecomError(_NOT_CONFIGURED)


def get_call(call_sid: str) -> dict:
    raise TelecomError(_NOT_CONFIGURED)


def list_calls(limit: int = 20) -> list[dict]:
    raise TelecomError(_NOT_CONFIGURED)


def download_recording(recording_url: str) -> bytes:
    raise TelecomError(_NOT_CONFIGURED)


def delete_recording(recording_sid: str) -> None:
    raise TelecomError(_NOT_CONFIGURED)
