from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.consent.models import GLOBAL_JURISDICTION, ConsentType
from app.consent.service import has_active_consent
from app.integrations.llm.groq import MODEL_VERSION as LLM_MODEL_VERSION
from app.integrations.llm.groq import extract_receptionist_qualification, summarize_transcript
from app.integrations.telecom.twilio import download_recording
from app.integrations.transcription.groq import MODEL_VERSION as TRANSCRIPTION_MODEL_VERSION
from app.integrations.transcription.groq import transcribe_audio
from app.intelligence.models import ConversationSummary, SummarySourceType
from app.media.models import CallRecord, Voicemail
from app.numbering.numbers.models import PhoneNumber

AI_DISCLAIMER = "AI-generated summary — may be inaccurate; not an authoritative record."


class SummaryAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the voicemail/call being summarized."""


class ConsentRequiredError(Exception):
    """Raised when AI processing is attempted without active consent on file."""


class NotRecordedError(Exception):
    """Raised when summarizing a call that has no recording yet (still in
    progress, wasn't forwarded, or the recording callback hasn't landed)."""


def _require_consent(db: Session, account_id: str, action: str, jurisdiction: str) -> None:
    if not has_active_consent(db, account_id, ConsentType.AI_PROCESSING, jurisdiction):
        raise ConsentRequiredError(
            f"AI processing consent is required before summarizing {action} — "
            "grant it via POST /compliance/consent first"
        )


def _phone_number_country(db: Session, phone_number_id: str | None) -> str:
    """Recording/AI-processing consent requirements genuinely differ by
    country, so consent is checked against the jurisdiction of the number
    involved - falling back to GLOBAL (the "applies everywhere" grant) when
    there's no number to derive one from."""
    if phone_number_id is None:
        return GLOBAL_JURISDICTION
    number = db.query(PhoneNumber).filter(PhoneNumber.id == phone_number_id).first()
    return number.country if number else GLOBAL_JURISDICTION


def _transcribe_and_store(
    db: Session, *, account_id: str, source_type: SummarySourceType, source_id: str, recording_url: str
) -> ConversationSummary:
    audio_bytes = download_recording(recording_url)
    transcript = transcribe_audio(audio_bytes)
    summary_text = summarize_transcript(transcript)

    record = ConversationSummary(
        account_id=account_id,
        source_type=source_type,
        source_id=source_id,
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
        metadata={"source_type": source_type.value, "source_id": source_id},
    )
    return record


def summarize_voicemail(db: Session, account_id: str, voicemail_id: str) -> ConversationSummary:
    voicemail = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
    if voicemail is None or voicemail.account_id != account_id:
        raise SummaryAuthorizationError(f"{voicemail_id} is not a voicemail owned by your account")

    jurisdiction = _phone_number_country(db, voicemail.phone_number_id)
    _require_consent(db, account_id, "voicemails", jurisdiction)

    return _transcribe_and_store(
        db,
        account_id=account_id,
        source_type=SummarySourceType.VOICEMAIL,
        source_id=voicemail.id,
        recording_url=voicemail.recording_url,
    )


def summarize_call(db: Session, account_id: str, call_id: str) -> ConversationSummary:
    call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
    if call is None or call.account_id != account_id:
        raise SummaryAuthorizationError(f"{call_id} is not a call owned by your account")
    if not call.recording_url:
        raise NotRecordedError(f"{call_id} has no recording yet — it may still be in progress")

    jurisdiction = _phone_number_country(db, call.phone_number_id)
    _require_consent(db, account_id, "calls", jurisdiction)

    return _transcribe_and_store(
        db,
        account_id=account_id,
        source_type=SummarySourceType.CALL,
        source_id=call.id,
        recording_url=call.recording_url,
    )


def qualify_caller(
    db: Session, account_id: str, transcript: str, jurisdiction: str = GLOBAL_JURISDICTION
) -> tuple[dict | None, str | None]:
    """Returns (qualification, model_version) — qualification is None when
    AI-processing consent hasn't been granted, rather than raising. Live
    call-handling code must always produce a TwiML response, so missing
    consent here means 'skip AI enrichment', not a hard failure.
    """
    if not has_active_consent(db, account_id, ConsentType.AI_PROCESSING, jurisdiction):
        return None, None
    return extract_receptionist_qualification(transcript), LLM_MODEL_VERSION


def list_account_summaries(db: Session, account_id: str) -> list[ConversationSummary]:
    return (
        db.query(ConversationSummary)
        .filter(ConversationSummary.account_id == account_id)
        .order_by(ConversationSummary.created_at.desc())
        .all()
    )
