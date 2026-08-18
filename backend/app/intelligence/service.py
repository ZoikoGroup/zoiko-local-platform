from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing import service as billing_service
from app.consent.models import GLOBAL_JURISDICTION, ConsentType
from app.consent.service import has_active_consent
from app.events.service import publish_ai_summary_completed, publish_transcript_completed
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.embeddings.cohere import EmbeddingError, generate_embedding
from app.integrations.llm.groq import MODEL_VERSION as LLM_MODEL_VERSION
from app.integrations.llm.groq import extract_conversation_summary, extract_receptionist_qualification
from app.integrations.storage.s3 import download_object
from app.integrations.telecom.twilio import download_recording
from app.integrations.transcription.groq import MODEL_VERSION as TRANSCRIPTION_MODEL_VERSION
from app.integrations.transcription.groq import transcribe_audio
from app.intelligence.models import AIJob, AIJobStatus, ConversationSummary, SummarySourceType
from app.media.models import CallDirection, CallRecord, ReceptionistUrgency, Voicemail, VideoSession
from app.notifications.service import notify_call_summary_available, notify_voicemail_transcription_ready
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.service import NumberConflictError, assert_number_access, assigned_number_ids
from app.numbering.numbers.models import PhoneNumber
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active
from app.retention.service import PURGED_MARKER
from app.usage import service as usage_service

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
    # Graceful degradation (Architecture doc §9) - AI features pause once a
    # payment grace period expires. Checked before spending a real Groq
    # call, not just before storing the result.
    billing_service.assert_billing_not_suspended(db, account_id)

    analysis = extract_conversation_summary(transcript)

    urgency_raw = analysis.get("urgency")
    urgency = ReceptionistUrgency(urgency_raw) if urgency_raw in ("low", "medium", "high") else None
    action_items = analysis.get("action_items")
    if not isinstance(action_items, list):
        action_items = None

    # analysis.get("summary", "") is NOT enough here - Groq's smaller model
    # (openai/gpt-oss-20b) sometimes returns an explicit `"summary": null`
    # for very short/low-content transcripts (e.g. "Hai, Helo, this is
    # Renky.") even though the prompt doesn't list null as a valid value for
    # this field. `.get(key, default)` only falls back when the KEY is
    # missing, not when its value is None - confirmed live: this crashed
    # every such call with a NOT NULL violation on conversation_summaries
    # .summary, surfaced to the caller as a generic "Service temporarily
    # unavailable" 503 (the DBAPIError handler in app.main), which hid the
    # real cause. summary is the one AI-extracted field with no nullable
    # column to fall back to (see ConversationSummary's docstring) - a real
    # fallback string, not an empty one, so the UI never renders a blank line.
    summary_text = analysis.get("summary") or "No summary could be generated for this recording."
    record = ConversationSummary(
        account_id=account_id,
        source_type=source_type,
        source_id=source_id,
        transcript=transcript,
        summary=summary_text,
        language=analysis.get("language"),
        urgency=urgency,
        action_items=action_items,
        suggested_follow_up=analysis.get("suggested_follow_up"),
        model_version=f"{TRANSCRIPTION_MODEL_VERSION};{LLM_MODEL_VERSION}",
    )

    # Real semantic search input - same fields the old full-text search
    # indexed (summary + transcript + follow-up), so search recall doesn't
    # regress. A Cohere failure degrades to no embedding for this record
    # (it just won't surface in semantic search) rather than failing the
    # whole summarize call - the summary itself is still real and useful.
    try:
        record.embedding = generate_embedding(
            f"{summary_text} {transcript} {record.suggested_follow_up or ''}", input_type="search_document"
        )
    except EmbeddingError:
        pass

    db.add(record)
    db.commit()
    db.refresh(record)
    _invalidate_summaries_cache(account_id)
    log_event(
        db, actor_id=account_id, action="intelligence.summary_created",
        target_type="conversation_summary", target_id=record.id,
        metadata={
            "source_type": source_type.value,
            "source_id": source_id,
            "urgency": urgency.value if urgency else None,
        },
    )
    publish_transcript_completed(
        account_id, summary_id=record.id, source_type=source_type.value, source_id=source_id,
        model_version=record.model_version,
    )
    publish_ai_summary_completed(
        account_id, summary_id=record.id, source_type=source_type.value, source_id=source_id,
        urgency=urgency.value if urgency else None,
    )

    # Usage Metering (Architecture doc §7/§5 "AI processing units") - one
    # unit per generated summary, keyed by the summary's own id so a retry
    # of the same summarize call (which always creates a new record, never
    # updates one in place) is correctly counted as new usage, not a duplicate.
    usage_service.record_usage_event(
        db, account_id=account_id, event_type="ai_summary", quantity=1, unit="summaries",
        country_band=None, idempotency_key=f"ai_summary:{record.id}",
    )
    return record


def _run_ai_job(
    db: Session, *, account_id: str, source_type: SummarySourceType, source_id: str, work_fn,
) -> ConversationSummary:
    """Retry lineage/failure observability wrapper (see AIJob's docstring) -
    one row per (source_type, source_id), updated in place across attempts
    rather than a new row each time, so a job's own history shows every
    attempt. Re-raises whatever work_fn raises after recording the
    failure - this changes nothing about caller-visible behavior/error
    responses, it only adds a persisted trail alongside them."""
    job = db.query(AIJob).filter(AIJob.source_type == source_type, AIJob.source_id == source_id).first()
    if job is None:
        job = AIJob(account_id=account_id, source_type=source_type, source_id=source_id, attempt_count=0, status=AIJobStatus.RUNNING)
        db.add(job)
    job.attempt_count += 1
    job.status = AIJobStatus.RUNNING
    job.last_error = None
    db.commit()

    try:
        result = work_fn()
    except Exception as e:
        job.status = AIJobStatus.FAILED
        job.last_error = str(e)[:2000]
        db.commit()
        raise
    job.status = AIJobStatus.SUCCEEDED
    job.conversation_summary_id = result.id
    db.commit()
    return result


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
    # Commercial Billing Operating Standard doc §32.1 - checked before any
    # transcription/LLM cost is actually incurred, not just before storing
    # the result (see _analyze_and_store's own billing-suspension gate for
    # the account-specific equivalent).
    assert_kill_switch_not_active(db, KillSwitchScope.AI_PROCESSING)

    voicemail = db.query(Voicemail).filter(Voicemail.id == voicemail_id).first()
    if voicemail is None or voicemail.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{voicemail_id} is not a voicemail owned by your account")
    _assert_can_access_number(db, user, voicemail.phone_number_id)

    jurisdiction = _phone_number_country(db, voicemail.phone_number_id)
    _require_consent(db, user.account_id, "voicemails", jurisdiction)

    summary = _run_ai_job(
        db, account_id=user.account_id, source_type=SummarySourceType.VOICEMAIL, source_id=voicemail.id,
        work_fn=lambda: _analyze_and_store(
            db, account_id=user.account_id, source_type=SummarySourceType.VOICEMAIL, source_id=voicemail.id,
            transcript=_download_and_transcribe(voicemail.recording_url),
        ),
    )
    notify_voicemail_transcription_ready(db, account_id=user.account_id, account_email=user.email)
    return summary


def summarize_call(db: Session, user: User, call_id: str) -> ConversationSummary:
    assert_kill_switch_not_active(db, KillSwitchScope.AI_PROCESSING)

    call = db.query(CallRecord).filter(CallRecord.id == call_id).first()
    if call is None or call.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{call_id} is not a call owned by your account")
    if not call.recording_url:
        raise NotRecordedError(f"{call_id} has no recording yet — it may still be in progress")
    _assert_can_access_number(db, user, call.phone_number_id)

    jurisdiction = _phone_number_country(db, call.phone_number_id)
    _require_consent(db, user.account_id, "calls", jurisdiction)

    summary = _run_ai_job(
        db, account_id=user.account_id, source_type=SummarySourceType.CALL, source_id=call.id,
        work_fn=lambda: _analyze_and_store(
            db, account_id=user.account_id, source_type=SummarySourceType.CALL, source_id=call.id,
            transcript=_download_and_transcribe(call.recording_url),
        ),
    )
    counterparty = call.to_number if call.direction == CallDirection.OUTBOUND else call.from_number
    notify_call_summary_available(db, account_id=user.account_id, account_email=user.email, counterparty=counterparty)
    return summary


def summarize_video_session(db: Session, user: User, room_name: str) -> ConversationSummary:
    """Keyed by room_name, not id - matches every other video action
    (join/end/recording), which are all addressed by room_name in the API,
    so the frontend never needs to know a session's internal id."""
    assert_kill_switch_not_active(db, KillSwitchScope.AI_PROCESSING)

    session = db.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    if session is None or session.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{room_name} is not a video session owned by your account")
    if user.role == UserRole.MEMBER and session.host_user_id != user.id:
        raise SummaryAuthorizationError(f"{room_name} was not hosted by you")
    if not session.recording_url or session.recording_url == PURGED_MARKER:
        raise NotRecordedError(f"{room_name} has no finished recording yet — it may still be in progress")

    _require_consent(db, user.account_id, "video calls", GLOBAL_JURISDICTION)

    return _run_ai_job(
        db, account_id=user.account_id, source_type=SummarySourceType.VIDEO, source_id=session.id,
        work_fn=lambda: _analyze_and_store(
            db, account_id=user.account_id, source_type=SummarySourceType.VIDEO, source_id=session.id,
            transcript=_download_and_transcribe_video(session.room_name),
        ),
    )


def edit_summary(db: Session, user: User, summary_id: str, new_summary: str) -> ConversationSummary:
    """AI governance requires "human-editable outputs," not just a
    disclaimer - this is that edit path. Access boundary mirrors whatever
    boundary applied when the summary was first created: a Member can only
    edit a summary whose underlying call/voicemail is on a number assigned
    to them, or a video session they hosted."""
    summary = db.query(ConversationSummary).filter(ConversationSummary.id == summary_id).first()
    if summary is None or summary.account_id != user.account_id:
        raise SummaryAuthorizationError(f"{summary_id} is not a summary owned by your account")

    if summary.source_type == SummarySourceType.CALL:
        call = db.query(CallRecord).filter(CallRecord.id == summary.source_id).first()
        if call is not None:
            _assert_can_access_number(db, user, call.phone_number_id)
    elif summary.source_type == SummarySourceType.VOICEMAIL:
        voicemail = db.query(Voicemail).filter(Voicemail.id == summary.source_id).first()
        if voicemail is not None:
            _assert_can_access_number(db, user, voicemail.phone_number_id)
    elif summary.source_type == SummarySourceType.VIDEO:
        session = db.query(VideoSession).filter(VideoSession.id == summary.source_id).first()
        if session is not None and user.role == UserRole.MEMBER and session.host_user_id != user.id:
            raise SummaryAuthorizationError(f"{summary_id} was not hosted by you")

    if summary.original_summary is None:
        summary.original_summary = summary.summary
    summary.summary = new_summary
    summary.edited_at = datetime.now(timezone.utc)
    summary.edited_by_user_id = user.id
    db.commit()
    db.refresh(summary)
    _invalidate_summaries_cache(summary.account_id)

    log_event(
        db, actor=user.id, action="intelligence.summary_edited",
        target=f"conversation_summary:{summary.id}", after={"edited": True},
    )
    return summary


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


def _summaries_cache_key(account_id: str) -> str:
    return f"summaries:list:{account_id}"


# Same "only cache the unfiltered Owner/Admin view" pattern as media.service's
# calls/voicemails/video-sessions caches - a Member's role-filtered view
# always queries live (see list_account_summaries below), since serving it
# from an unfiltered cached page could return rows the Member shouldn't see.
# The embedding vector is deliberately excluded from the cached payload -
# _summary_response (the only consumer of this list) never reads it, and
# caching a float array per row for semantic search (a separate, uncached
# query path - see search_account_summaries) would be pure waste.
_SUMMARIES_CACHE_TTL_SECONDS = 20


def _serialize_summary(s: ConversationSummary) -> dict:
    return {
        "id": s.id,
        "account_id": s.account_id,
        "source_type": s.source_type.value,
        "source_id": s.source_id,
        "transcript": s.transcript,
        "summary": s.summary,
        "language": s.language,
        "urgency": s.urgency.value if s.urgency else None,
        "action_items": s.action_items,
        "suggested_follow_up": s.suggested_follow_up,
        "model_version": s.model_version,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "original_summary": s.original_summary,
        "edited_at": s.edited_at.isoformat() if s.edited_at else None,
        "edited_by_user_id": s.edited_by_user_id,
    }


def _deserialize_summary(data: dict) -> ConversationSummary:
    return ConversationSummary(
        id=data["id"],
        account_id=data["account_id"],
        source_type=SummarySourceType(data["source_type"]),
        source_id=data["source_id"],
        transcript=data["transcript"],
        summary=data["summary"],
        language=data["language"],
        urgency=ReceptionistUrgency(data["urgency"]) if data["urgency"] else None,
        action_items=data["action_items"],
        suggested_follow_up=data["suggested_follow_up"],
        model_version=data["model_version"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
        original_summary=data["original_summary"],
        edited_at=datetime.fromisoformat(data["edited_at"]) if data["edited_at"] else None,
        edited_by_user_id=data["edited_by_user_id"],
    )


def _invalidate_summaries_cache(account_id: str | None) -> None:
    if account_id:
        cache_delete(_summaries_cache_key(account_id))


def list_account_summaries(db: Session, user: User) -> list[ConversationSummary]:
    ids = assigned_number_ids(db, user)
    if ids is not None:
        # Member view - always queries live, same reason as list_account_calls.
        return _account_summaries_query(db, user).order_by(ConversationSummary.created_at.desc()).all()

    cache_key = _summaries_cache_key(user.account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_summary(row) for row in cached]
    summaries = _account_summaries_query(db, user).order_by(ConversationSummary.created_at.desc()).all()
    cache_set(cache_key, [_serialize_summary(s) for s in summaries], ttl_seconds=_SUMMARIES_CACHE_TTL_SECONDS)
    return summaries


# Cosine distance cutoff below which a result counts as a real semantic
# match. Calibrated against real Cohere embeddings, not guessed: genuinely
# related pairs (e.g. "connectivity issue" query against a summary saying
# "the internet is down") measured 0.47-0.76; unrelated pairs measured
# 0.84-0.90. 0.80 sits in the gap between them with roughly even margin on
# both sides.
_SEMANTIC_DISTANCE_THRESHOLD = 0.80
_SEMANTIC_RESULT_LIMIT = 20


def search_account_summaries(db: Session, user: User, search_query: str) -> list[ConversationSummary]:
    """Real semantic search via pgvector cosine similarity - finds records
    by MEANING, not shared words. A summary saying "the internet is down"
    is found by searching "connectivity issue" even though they share no
    words, which the old keyword-based full-text search could never do.
    Degrades to no results (not an error) if Cohere is unreachable, or for
    any record whose embedding failed at creation time.
    """
    try:
        query_embedding = generate_embedding(search_query, input_type="search_query")
    except EmbeddingError:
        return []

    distance = ConversationSummary.embedding.cosine_distance(query_embedding)
    return (
        _account_summaries_query(db, user)
        .filter(ConversationSummary.embedding.isnot(None))
        .filter(distance < _SEMANTIC_DISTANCE_THRESHOLD)
        .order_by(distance.asc())
        .limit(_SEMANTIC_RESULT_LIMIT)
        .all()
    )


def list_ai_jobs(db: Session, status_filter: AIJobStatus | None = None) -> list[AIJob]:
    """Staff-only failure-observability view (AIJob's docstring) - any
    staff role can view, same diagnostic posture as /ops/traces and the
    risk score view."""
    query = db.query(AIJob)
    if status_filter is not None:
        query = query.filter(AIJob.status == status_filter)
    return query.order_by(AIJob.updated_at.desc()).all()
