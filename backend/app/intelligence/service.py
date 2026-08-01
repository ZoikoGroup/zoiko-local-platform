from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.consent.models import ConsentType
from app.consent.service import has_active_consent
from app.integrations.llm.groq import MODEL_VERSION as LLM_MODEL_VERSION
from app.integrations.llm.groq import extract_receptionist_qualification, summarize_transcript
from app.integrations.telecom.twilio import download_recording
from app.integrations.transcription.groq import MODEL_VERSION as TRANSCRIPTION_MODEL_VERSION
from app.integrations.transcription.groq import transcribe_audio
from app.intelligence.models import ConversationSummary, SummarySourceType
from app.media.models import Voicemail

AI_DISCLAIMER = "AI-generated summary — may be inaccurate; not an authoritative record."


class SummaryAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the voicemail being summarized."""


class ConsentRequiredError(Exception):
    """Raised when AI processing is attempted without active consent on file."""


def summarize_voicemail(db: Session, account_id: str, voicemail_id: str) -> ConversationSummary:
    voicemail = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
    if voicemail is None or voicemail.account_id != account_id:
        raise SummaryAuthorizationError(f"{voicemail_id} is not a voicemail owned by your account")

    if not has_active_consent(db, account_id, ConsentType.AI_PROCESSING):
        raise ConsentRequiredError(
            "AI processing consent is required before summarizing voicemails — "
            "grant it via POST /compliance/consent first"
        )

    audio_bytes = download_recording(voicemail.recording_url)
    transcript = transcribe_audio(audio_bytes)
    summary_text = summarize_transcript(transcript)

    record = ConversationSummary(
        account_id=account_id,
        source_type=SummarySourceType.VOICEMAIL,
        source_id=voicemail.id,
        transcript=transcript,
        summary=summary_text,
        model_version=f"{TRANSCRIPTION_MODEL_VERSION};{LLM_MODEL_VERSION}",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_event(
        db, actor_id=account_id, action="intelligence.summary_created",
        target_type="conversation_summary", target_id=record.id,
        metadata={"source_type": "voicemail", "source_id": voicemail.id},
    )
    return record


def qualify_caller(db: Session, account_id: str, transcript: str) -> tuple[dict | None, str | None]:
    """Returns (qualification, model_version) — qualification is None when
    AI-processing consent hasn't been granted, rather than raising. Live
    call-handling code must always produce a TwiML response, so missing
    consent here means 'skip AI enrichment', not a hard failure.
    """
    if not has_active_consent(db, account_id, ConsentType.AI_PROCESSING):
        return None, None
    return extract_receptionist_qualification(transcript), LLM_MODEL_VERSION


def list_account_summaries(db: Session, account_id: str) -> list[ConversationSummary]:
    return (
        db.query(ConversationSummary)
        .filter(ConversationSummary.account_id == account_id)
        .order_by(ConversationSummary.created_at.desc())
        .all()
    )
