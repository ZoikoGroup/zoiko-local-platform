"""Stand-in for a second transcription vendor behind
transcription_failover_enabled. No real second-vendor account exists yet -
raises a clearly labeled error instead of silently no-opping.
"""

from app.integrations.transcription.groq import TranscriptionError

_NOT_CONFIGURED = (
    "secondary transcription provider not configured - set "
    "TRANSCRIPTION_SECONDARY_* credentials once a second vendor account exists"
)


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.wav", content_type: str = "audio/wav") -> str:
    raise TranscriptionError(_NOT_CONFIGURED)
