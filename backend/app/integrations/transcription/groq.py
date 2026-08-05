"""
Provider Gateway for Groq's Whisper transcription API (transcription
category). Only file allowed to call this vendor's API directly.
"""

import httpx

from app.core.config import settings
from app.observability.service import trace_provider_call

_TRANSCRIPTIONS_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL_VERSION = "groq/whisper-large-v3"


class TranscriptionError(Exception):
    """Raised instead of letting an httpx/vendor-specific exception escape this module."""


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.wav", content_type: str = "audio/wav") -> str:
    if not settings.groq_api_key:
        raise TranscriptionError("Groq API key is not configured")

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
