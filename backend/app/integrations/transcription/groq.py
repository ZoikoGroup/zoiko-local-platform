"""
Provider Gateway for Groq's Whisper transcription API (transcription
category). Only file allowed to call this vendor's API directly.
"""

import httpx

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover
from app.observability.service import trace_provider_call

_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL_VERSION = "groq/whisper-large-v3"

_breaker = CircuitBreaker("transcription")


def circuit_state() -> str:
    return _breaker.state.value


class TranscriptionError(Exception):
    """Raised instead of letting an httpx/vendor-specific exception escape this module."""


def _is_provider_failure(e: Exception) -> bool:
    """Passed as with_failover's is_breaker_failure - _breaker is a single
    process-wide instance shared by every transcription request on the
    platform, so what counts as a "failure" here matters beyond just this
    one request. Every TranscriptionError raised in this module wraps the
    original httpx exception via `from e`, so e.__cause__ is that original
    exception.

    An httpx.HTTPStatusError carries the real HTTP status Groq returned via
    `.response.status_code`. A 4xx means Groq understood and rejected THIS
    specific request (e.g. malformed/unsupported audio) - an expected,
    per-request outcome that says nothing about whether Groq itself is
    healthy. An httpx.RequestError (timeout, connection failure, DNS
    failure, ...) has no `.response` at all - nothing HTTP to inspect, which
    does count as a real provider-health signal. Only a 5xx (or no status at
    all) should trip the shared breaker - same conservative default as
    twilio.py's _is_provider_failure."""
    cause = getattr(e, "__cause__", None)
    status = getattr(getattr(cause, "response", None), "status_code", None)
    return status is None or status >= 500


# Imported after TranscriptionError is defined - _secondary_stub imports it
# back from this module, which would otherwise be a circular import.
from app.integrations.transcription import _secondary_stub as secondary  # noqa: E402


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.wav", content_type: str = "audio/wav") -> str:
    if not settings.groq_api_key:
        raise TranscriptionError("Groq API key is not configured")

    def _primary() -> str:
        try:
            with trace_provider_call("groq_transcription", "transcribe_audio"):
                response = httpx.post(
                    _TRANSCRIPTIONS_URL,
                    headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                    data={"model": "whisper-large-v3"},  # keep literal in sync with MODEL_VERSION above
                    files={"file": (filename, audio_bytes, content_type)},
                    timeout=60.0,
                )
                response.raise_for_status()
        except httpx.HTTPError as e:
            raise TranscriptionError(f"Groq transcription request failed: {e}") from e

        return response.json()["text"]

    secondary_fn = (
        (lambda: secondary.transcribe_audio(audio_bytes, filename, content_type))
        if settings.transcription_failover_enabled else None
    )
    return with_failover(_breaker, _primary, secondary_fn, TranscriptionError, _is_provider_failure)
