"""
Provider Gateway for Groq's Whisper transcription API (transcription
category). Only file allowed to call this vendor's API directly.
"""

import httpx

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover

_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL_VERSION = "groq/whisper-large-v3"

_breaker = CircuitBreaker("transcription")


def circuit_state() -> str:
    return _breaker.state.value


class TranscriptionError(Exception):
    """Raised instead of letting an httpx/vendor-specific exception escape this module."""


# Imported after TranscriptionError is defined - _secondary_stub imports it
# back from this module, which would otherwise be a circular import.
from app.integrations.transcription import _secondary_stub as secondary  # noqa: E402


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.wav", content_type: str = "audio/wav") -> str:
    if not settings.groq_api_key:
        raise TranscriptionError("Groq API key is not configured")

    def _primary() -> str:
        try:
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
    return with_failover(_breaker, _primary, secondary_fn, TranscriptionError)
