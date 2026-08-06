"""Secondary transcription vendor (Deepgram) behind
transcription_failover_enabled. Real API calls, not a mock - but NOT tested
against a live account, since no real Deepgram credentials exist yet. Wire
DEEPGRAM_API_KEY in .env and flip TRANSCRIPTION_FAILOVER_ENABLED=true to
activate. Callers in groq.py never change, since it dispatches to this
module by function name only.
"""

import httpx

from app.core.config import settings
from app.integrations.transcription.groq import TranscriptionError

_TRANSCRIBE_URL = "https://api.deepgram.com/v1/listen"


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.wav", content_type: str = "audio/wav") -> str:
    if not settings.deepgram_api_key:
        raise TranscriptionError("Secondary transcription provider (Deepgram) is not configured - set DEEPGRAM_API_KEY")

    try:
        response = httpx.post(
            _TRANSCRIBE_URL,
            headers={"Authorization": f"Token {settings.deepgram_api_key}", "Content-Type": content_type},
            content=audio_bytes,
            params={"model": "nova-2", "smart_format": "true"},
            timeout=60.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise TranscriptionError(f"Deepgram transcription request failed: {e}") from e

    try:
        return response.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    except (KeyError, IndexError) as e:
        raise TranscriptionError(f"Deepgram returned an unexpected response shape: {response.text!r}") from e
