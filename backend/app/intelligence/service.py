import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.consent.models import GLOBAL_JURISDICTION, ConsentType
from app.consent.service import has_active_consent
from app.integrations.llm.groq import MODEL_VERSION as LLM_MODEL_VERSION
from app.integrations.llm.groq import extract_conversation_summary, extract_receptionist_qualification
from app.integrations.storage.s3 import download_object
from app.integrations.telecom.twilio import download_recording
from app.integrations.transcription.groq import MODEL_VERSION as TRANSCRIPTION_MODEL_VERSION
from app.integrations.transcription.groq import transcribe_audio
from app.intelligence.models import ConversationSummary, SummarySourceType
from app.media.models import CallRecord, ReceptionistUrgency, Voicemail, VideoSession
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.service import NumberConflictError, assert_number_access, assigned_number_ids
from app.numbering.numbers.models import PhoneNumber
from app.retention.service import PURGED_MARKER

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


def _download_and_transcribe(recording_url: str) -> str:
    audio_bytes = download_recording(recording_url)
    return transcribe_audio(audio_bytes)


def _download_and_transcribe_video(room_name: str) -> str:
    """Video recordings live in object storage as the room's egress output,
    not behind a Twilio-authenticated URL like calls/voicemail - fetched by
    bucket key instead, and handed to Whisper with a video content-type
    (Groq's transcription endpoint accepts mp4 directly, no audio
    extraction step needed)."""
    audio_bytes = download_object(f"recordings/{room_name}.mp4")
    return transcribe_audio(audio_bytes, filename=f"{room_name}.mp4", content_type="video/mp4")


def _analyze_and_store(
    db: Session, *, account_id: str, source_type: SummarySourceType, source_id: str, transcript: str
) -> ConversationSummary:
    analysis = extract_conversation_summary(transcript)

    urgency_raw = analysis.get("urgency")
    urgency = ReceptionistUrgency(urgency_raw) if urgency_raw in ("low", "medium", "high") else None
    action_items = analysis.get("action_items")
    if not isinstance(action_items, list):
        action_items = None

    record = ConversationSummary(
        account_id=account_id,
        source_type=source_type,
        source_id=source_id,
        transcript=transcript,
        summary=analysis.get("summary", ""),
        language=analysis.get("language"),
        urgency=urgency,
        action_items=action_items,
        suggested_follow_up=analysis.get("suggested_follow_up"),
        model_version=f"{TRANSCRIPTION_MODEL_VERSION};{LLM_MODEL_VERSION}",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    log_event(
        db, actor_id=account_id, action="intelligence.summary_created",
        target_type="conversation_summary", target_id=record.id,
        metadata={
            "source_type": source_type.value,
            "source_id": source_id,
            "urgency": urgency.value if urgency else None,
        },
    )
    return record


def _assert_can_access_number(db: Session, user: User, phone_number_id: str | None) -> None:
    """A Member may only summarize calls/voicemail on a number assigned to
    them - same assignment boundary as direct number management."""
    if phone_number_id is None:
        return
    number = db.query(PhoneNumber).filter(PhoneNumber.id == phone_number_id).first()
    if number is None:
        return
    try:
        assert_number_access(number, user)
    except NumberConflictError as e:
        raise SummaryAuthorizationError(str(e)) from e


def summarize_voicemail(db: Session, user: User, voicemail_id: str) -> ConversationSummary:
    voicemail = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
    if voicemail is None or voicemail.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{voicemail_id} is not a voicemail owned by your account")
    _assert_can_access_number(db, user, voicemail.phone_number_id)

    jurisdiction = _phone_number_country(db, voicemail.phone_number_id)
    _require_consent(db, user.account_id, "voicemails", jurisdiction)

    return _analyze_and_store(
        db,
        account_id=user.account_id,
        source_type=SummarySourceType.VOICEMAIL,
        source_id=voicemail.id,
        transcript=_download_and_transcribe(voicemail.recording_url),
    )


def summarize_call(db: Session, user: User, call_id: str) -> ConversationSummary:
    call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
    if call is None or call.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{call_id} is not a call owned by your account")
    if not call.recording_url:
        raise NotRecordedError(f"{call_id} has no recording yet — it may still be in progress")
    _assert_can_access_number(db, user, call.phone_number_id)

    jurisdiction = _phone_number_country(db, call.phone_number_id)
    _require_consent(db, user.account_id, "calls", jurisdiction)

    return _analyze_and_store(
        db,
        account_id=user.account_id,
        source_type=SummarySourceType.CALL,
        source_id=call.id,
        transcript=_download_and_transcribe(call.recording_url),
    )


def summarize_video_session(db: Session, user: User, room_name: str) -> ConversationSummary:
    """Keyed by room_name, not id - matches every other video action
    (join/end/recording), which are all addressed by room_name in the API,
    so the frontend never needs to know a session's internal id."""
    session = db.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    if session is None or session.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{room_name} is not a video session owned by your account")
    if user.role == UserRole.MEMBER and session.host_user_id != user.id:
        raise SummaryAuthorizationError(f"{room_name} was not hosted by you")
    if not session.recording_url or session.recording_url == PURGED_MARKER:
        raise NotRecordedError(f"{room_name} has no finished recording yet — it may still be in progress")

    _require_consent(db, user.account_id, "video calls", GLOBAL_JURISDICTION)

    return _analyze_and_store(
        db,
        account_id=user.account_id,
        source_type=SummarySourceType.VIDEO,
        source_id=session.id,
        transcript=_download_and_transcribe_video(session.room_name),
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


def _account_summaries_query(db: Session, user: User):
    """Owner/Admin see every summary on the account. A plain Member only
    sees summaries whose underlying call/voicemail is on a number assigned
    to them, or - for video - whose session they personally hosted (video
    sessions aren't tied to a number). Shared base query for both listing
    and search."""
    query = db.query(ConversationSummary).filter(ConversationSummary.account_id == user.account_id)

    ids = assigned_number_ids(db, user)
    if ids is not None:
        allowed_call_ids = {
            row[0] for row in db.query(CallRecord.id).filter(CallRecord.phone_number_id.in_(ids)).all()
        }
        allowed_voicemail_ids = {
            row[0] for row in db.query(Voicemail.id).filter(Voicemail.phone_number_id.in_(ids)).all()
        }
        allowed_video_ids = {
            row[0]
            for row in db.query(VideoSession.id)
            .filter(VideoSession.account_id == user.account_id, VideoSession.host_user_id == user.id)
            .all()
        }
        query = query.filter(
            sa.or_(
                sa.and_(
                    ConversationSummary.source_type == SummarySourceType.VIDEO,
                    ConversationSummary.source_id.in_(allowed_video_ids),
                ),
                sa.and_(
                    ConversationSummary.source_type == SummarySourceType.CALL,
                    ConversationSummary.source_id.in_(allowed_call_ids),
                ),
                sa.and_(
                    ConversationSummary.source_type == SummarySourceType.VOICEMAIL,
                    ConversationSummary.source_id.in_(allowed_voicemail_ids),
                ),
            )
        )
    return query


def list_account_summaries(db: Session, user: User) -> list[ConversationSummary]:
    return _account_summaries_query(db, user).order_by(ConversationSummary.created_at.desc()).all()


def search_account_summaries(db: Session, user: User, search_query: str) -> list[ConversationSummary]:
    """Real Postgres full-text search over each summary's transcript +
    summary + suggested follow-up, ranked by relevance - not true
    meaning-based (ML embedding) search, but genuinely searches by content
    rather than just scrolling a chronological list. websearch_to_tsquery
    parses natural free-text input (quotes, "or", "-exclude") the way a
    search engine would, rather than requiring tsquery's own operator syntax.
    """
    document = sa.func.to_tsvector(
        "english",
        sa.func.concat(
            ConversationSummary.summary, " ", ConversationSummary.transcript, " ",
            sa.func.coalesce(ConversationSummary.suggested_follow_up, ""),
        ),
    )
    tsquery = sa.func.websearch_to_tsquery("english", search_query)

    return (
        _account_summaries_query(db, user)
        .filter(document.op("@@")(tsquery))
        .order_by(sa.func.ts_rank(document, tsquery).desc())
        .all()
    )
