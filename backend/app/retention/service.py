from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.contacts.models import Contact
from app.events.service import (
    publish_retention_erasure_requested,
    publish_retention_policy_set,
    publish_retention_recording_purged,
)
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.storage.s3 import StorageError, delete_object
from app.integrations.telecom import twilio as telecom
from app.integrations.telecom.twilio import TelecomError
from app.intelligence.models import ConversationSummary
from app.media.models import CallRecord, ReceptionistCall, VideoSession, Voicemail
from app.numbering.identity.models import Account
from app.retention.models import ArtifactType, ErasureRequest, ErasureRequestStatus, RetentionPolicy

# Safety-net fallback when no policy row exists at all (shouldn't normally
# happen once the migration seeds global defaults, but never leave retention
# undefined - roadmap doc requires it be "retained by policy", not forever).
DEFAULT_RETENTION_DAYS = 90

PURGED_MARKER = "[deleted - retention policy]"
# A failed/lost video egress used to leave recording_egress_id set forever
# with recording_url still None - see media.service.sweep_stale_video_
# recordings and _handle_egress_ended, both of which set this marker.
# Defined here (not in media.service) to avoid a circular import - media.
# service already imports PURGED_MARKER from this module.
RECORDING_FAILED_MARKER = "[recording failed]"
# Distinct from PURGED_MARKER (a routine, policy-driven purge) - this is a
# customer-rights-driven erasure (see erase_account_data), a different
# legal basis worth being able to tell apart in the data itself, not just
# in the audit log.
ERASED_MARKER = "[erased - right to erasure]"


def is_account_under_legal_hold(db: Session, account_id: str | None) -> bool:
    """Architecture doc §10 "legal hold model for business customers" -
    checked at the top of every purge loop below, before the normal
    retention-window check even runs. A hold blocks purge regardless of
    how overdue the recording otherwise is - staff set this via
    app.staff.service.set_account_legal_hold specifically to stop a
    scheduled sweep from destroying evidence mid-litigation."""
    if account_id is None:
        return False
    from app.numbering.identity.models import Account

    account = db.query(Account).filter(Account.id == account_id).first()
    return account is not None and account.legal_hold


def get_retention_days(db: Session, account_id: str, artifact_type: ArtifactType) -> int:
    account_policy = (
        db.query(RetentionPolicy)
        .filter(RetentionPolicy.account_id == account_id, RetentionPolicy.artifact_type == artifact_type)
        .first()
    )
    if account_policy:
        return account_policy.retention_days

    global_policy = (
        db.query(RetentionPolicy)
        .filter(RetentionPolicy.account_id.is_(None), RetentionPolicy.artifact_type == artifact_type)
        .first()
    )
    if global_policy:
        return global_policy.retention_days

    return DEFAULT_RETENTION_DAYS


def set_retention_policy(
    db: Session, *, account_id: str, artifact_type: ArtifactType, retention_days: int, actor: str
) -> RetentionPolicy:
    if retention_days < 1:
        raise ValueError("retention_days must be at least 1")

    policy = (
        db.query(RetentionPolicy)
        .filter(RetentionPolicy.account_id == account_id, RetentionPolicy.artifact_type == artifact_type)
        .first()
    )
    if policy is None:
        policy = RetentionPolicy(account_id=account_id, artifact_type=artifact_type, retention_days=retention_days)
        db.add(policy)
    else:
        policy.retention_days = retention_days
    db.commit()
    db.refresh(policy)
    log_event(
        db, actor=actor, action="retention.policy_set",
        target=f"retention_policy:{policy.id}",
        after={"artifact_type": artifact_type.value, "retention_days": retention_days},
    )
    publish_retention_policy_set(account_id, artifact_type=artifact_type.value, retention_days=retention_days)
    return policy


def _retention_policies_cache_key(account_id: str) -> str:
    return f"retention_policies:list:{account_id}"


# Real N+1 cost, same shape as routing.list_flows: get_retention_days runs
# up to 2 queries (account override, global fallback) PER artifact type, so
# a straight call here is up to 2 * len(ArtifactType) queries for what's
# conceptually a single small settings view.
_RETENTION_POLICIES_CACHE_TTL_SECONDS = 30


def _invalidate_retention_policies_cache(account_id: str) -> None:
    cache_delete(_retention_policies_cache_key(account_id))


def list_retention_policies(db: Session, account_id: str) -> dict[str, int]:
    """Returns the EFFECTIVE retention (account override, else global
    default, else the hardcoded fallback) for every artifact type - so a
    customer always sees a real number, never a gap."""
    cache_key = _retention_policies_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = {t.value: get_retention_days(db, account_id, t) for t in ArtifactType}
    cache_set(cache_key, result, ttl_seconds=_RETENTION_POLICIES_CACHE_TTL_SECONDS)
    return result


def _purge_voicemails(
    db: Session, now: datetime, *, account_id: str | None = None, force: bool = False
) -> tuple[int, int]:
    purged = failed = 0
    query = db.query(Voicemail).filter(Voicemail.recording_url.isnot(None))
    if account_id is not None:
        query = query.filter(Voicemail.account_id == account_id)
    for vm in query.all():
        if is_account_under_legal_hold(db, vm.account_id):
            continue
        retention_days = get_retention_days(db, vm.account_id, ArtifactType.VOICEMAIL)
        if not force and vm.created_at >= now - timedelta(days=retention_days):
            continue
        try:
            telecom.delete_recording(telecom.recording_sid_from_url(vm.recording_url))
        except TelecomError as e:
            log_event(
                db, actor_id=vm.account_id, action="retention.purge_failed",
                target_type="voicemail", target_id=vm.id, reason=str(e),
            )
            failed += 1
            continue
        vm.recording_url = PURGED_MARKER
        db.commit()
        if vm.account_id:
            from app.media.service import _invalidate_voicemails_cache

            _invalidate_voicemails_cache(vm.account_id)
        log_event(
            db, actor_id=vm.account_id, action="retention.voicemail_purged",
            target_type="voicemail", target_id=vm.id, metadata={"retention_days": retention_days},
        )
        publish_retention_recording_purged(vm.account_id, artifact_type="voicemail", target_id=vm.id)
        purged += 1
    return purged, failed


def _purge_call_recordings(
    db: Session, now: datetime, *, account_id: str | None = None, force: bool = False
) -> tuple[int, int]:
    purged = failed = 0
    query = db.query(CallRecord).filter(CallRecord.recording_url.isnot(None), CallRecord.account_id.isnot(None))
    if account_id is not None:
        query = query.filter(CallRecord.account_id == account_id)
    for call in query.all():
        if is_account_under_legal_hold(db, call.account_id):
            continue
        retention_days = get_retention_days(db, call.account_id, ArtifactType.CALL_RECORDING)
        if not force and call.created_at >= now - timedelta(days=retention_days):
            continue
        try:
            telecom.delete_recording(telecom.recording_sid_from_url(call.recording_url))
        except TelecomError as e:
            log_event(
                db, actor_id=call.account_id, action="retention.purge_failed",
                target_type="call_record", target_id=call.id, reason=str(e),
            )
            failed += 1
            continue
        call.recording_url = PURGED_MARKER
        db.commit()
        if call.account_id:
            # Deferred import - app.media.service imports this module
            # (for PURGED_MARKER), so a module-level import here would be
            # circular.
            from app.media.service import _invalidate_calls_cache

            _invalidate_calls_cache(call.account_id)
        log_event(
            db, actor_id=call.account_id, action="retention.call_recording_purged",
            target_type="call_record", target_id=call.id, metadata={"retention_days": retention_days},
        )
        publish_retention_recording_purged(call.account_id, artifact_type="call_recording", target_id=call.id)
        purged += 1
    return purged, failed


def _purge_video_recordings(
    db: Session, now: datetime, *, account_id: str | None = None, force: bool = False
) -> tuple[int, int]:
    purged = failed = 0
    query = db.query(VideoSession).filter(
        VideoSession.recording_url.isnot(None), VideoSession.recording_url != RECORDING_FAILED_MARKER,
    )
    if account_id is not None:
        query = query.filter(VideoSession.account_id == account_id)
    for session in query.all():
        if is_account_under_legal_hold(db, session.account_id):
            continue
        retention_days = get_retention_days(db, session.account_id, ArtifactType.VIDEO_RECORDING)
        reference_time = session.ended_at or session.started_at or session.created_at
        if not force and reference_time >= now - timedelta(days=retention_days):
            continue
        # recording_object_key is the actual key it was uploaded under -
        # older sessions predating that column fall back to the room_name-
        # based scheme, since that's genuinely what those files are keyed by.
        object_key = session.recording_object_key or f"recordings/{session.room_name}.mp4"
        try:
            delete_object(object_key)
        except StorageError as e:
            log_event(
                db, actor_id=session.account_id, action="retention.purge_failed",
                target_type="video_session", target_id=session.id, reason=str(e),
            )
            failed += 1
            continue
        session.recording_url = PURGED_MARKER
        db.commit()
        log_event(
            db, actor_id=session.account_id, action="retention.video_recording_purged",
            target_type="video_session", target_id=session.id, metadata={"retention_days": retention_days},
        )
        publish_retention_recording_purged(session.account_id, artifact_type="video_recording", target_id=session.id)
        purged += 1
    return purged, failed


def purge_expired_recordings(db: Session) -> dict[str, dict[str, int]]:
    """Finds every voicemail/call/video recording past its account's
    retention window, deletes the underlying file from the real provider
    (Twilio or S3-compatible storage), and clears our own recording_url so
    the app stops linking to something that's gone. The metadata row itself
    is kept - roadmap doc's "linked to immutable audit records" requirement
    covers the audit trail, not the raw media.

    Called from app.ops.scheduled_reconciliation's daily run - still
    depends on that script actually being scheduled externally (see its own
    docstring), this function has no timer of its own.

    A failed deletion (provider unreachable, already gone, etc.) leaves
    recording_url untouched so it's retried next run, and logs its own
    audit event rather than failing silently - matches the doc's "no silent
    failure" principle.
    """
    now = datetime.now(timezone.utc)
    voicemail_purged, voicemail_failed = _purge_voicemails(db, now)
    call_purged, call_failed = _purge_call_recordings(db, now)
    video_purged, video_failed = _purge_video_recordings(db, now)
    return {
        "voicemail": {"purged": voicemail_purged, "failed": voicemail_failed},
        "call_recording": {"purged": call_purged, "failed": call_failed},
        "video_recording": {"purged": video_purged, "failed": video_failed},
    }


class ErasureRequestNotFoundError(Exception):
    """Raised resolving an erasure request that doesn't exist."""


class ErasureRequestNotPendingError(Exception):
    """Raised when trying to resolve an erasure request that isn't
    currently PENDING - a request can only be resolved once."""


class AccountUnderLegalHoldError(Exception):
    """Raised by create_erasure_request (fail fast - don't let a request be
    opened that can never be completed) and by erase_account_data (the
    same guardrail that already blocks every scheduled purge,
    is_account_under_legal_hold, must also block a customer-rights
    erasure, not just a routine retention-window purge) when the account
    has an active legal hold. Staff must clear the hold
    (app.staff.service.set_account_legal_hold) before an erasure request
    on this account can be opened or completed."""


def erase_account_data(db: Session, account_id: str, *, actor: str) -> dict[str, int]:
    """Architecture doc §10 "right-to-erasure workflow" - the actual
    cascade that resolve_erasure_request's COMPLETED status previously
    only ASSUMED a human had carried out by hand, through unspecified
    "domain tools" that didn't actually exist anywhere in this codebase
    (real gap found in a full-backend audit). Does the following, for
    real, right now (not on the 90-day retention-window schedule the daily
    sweep uses - see purge_expired_recordings):

    - Refuses outright if the account is under legal hold (same guardrail
      every scheduled purge already respects - a hold must block an
      erasure just as hard as it blocks a routine purge).
    - Force-purges every voicemail/call/video recording immediately,
      bypassing the normal retention_days age check (this is a rights-
      driven immediate deletion, not a "it's finally old enough" sweep).
    - Deletes the account's contacts outright (no billing/audit tie).
    - Deletes the account's AI conversation summaries outright (no
      billing/audit tie - the underlying recording is erased above).
    - Redacts (not deletes - see ReceptionistCall.duration_seconds's role
      in ai_receptionist_minutes billing) every PII field on the account's
      AI Receptionist call records.

    Deliberately does NOT touch CallRecord's own from_number/to_number/
    duration/status columns, or usage/billing records - those are the
    account's own billing and audit history, not the kind of "erasable"
    data a DSAR right-to-erasure request reaches; the model's own PII
    (the recording) is what gets erased above.
    """
    if is_account_under_legal_hold(db, account_id):
        raise AccountUnderLegalHoldError(
            f"Account {account_id!r} is under legal hold - clear the hold before this erasure request can be completed."
        )

    now = datetime.now(timezone.utc)
    voicemail_purged, voicemail_failed = _purge_voicemails(db, now, account_id=account_id, force=True)
    call_purged, call_failed = _purge_call_recordings(db, now, account_id=account_id, force=True)
    video_purged, video_failed = _purge_video_recordings(db, now, account_id=account_id, force=True)

    contacts_deleted = (
        db.query(Contact).filter(Contact.account_id == account_id).delete(synchronize_session=False)
    )
    summaries_deleted = (
        db.query(ConversationSummary).filter(ConversationSummary.account_id == account_id).delete(synchronize_session=False)
    )

    receptionist_calls_redacted = 0
    for call in db.query(ReceptionistCall).filter(ReceptionistCall.account_id == account_id).all():
        call.raw_transcript = ERASED_MARKER
        call.caller_name = None
        call.caller_company = None
        call.reason = None
        call.summary = None
        call.caller_number = "[erased]"  # caller_number is String(20) - too short for ERASED_MARKER
        receptionist_calls_redacted += 1

    db.commit()
    result = {
        "voicemails_purged": voicemail_purged, "voicemails_failed": voicemail_failed,
        "call_recordings_purged": call_purged, "call_recordings_failed": call_failed,
        "video_recordings_purged": video_purged, "video_recordings_failed": video_failed,
        "contacts_deleted": contacts_deleted, "summaries_deleted": summaries_deleted,
        "receptionist_calls_redacted": receptionist_calls_redacted,
    }
    log_event(
        db, actor_id=account_id, action="retention.account_data_erased",
        target_type="account", target_id=account_id, metadata={**result, "erased_by": actor},
    )
    return result


def create_erasure_request(db: Session, *, account_id: str, requested_by: str, notes: str | None = None) -> ErasureRequest:
    """Architecture doc §10 "right-to-erasure workflow" - customer-
    initiated, not automatic. Opens a staff-visible request; a human
    decides what's actually erasable (some records must legally be
    retained - billing/tax evidence, an open compliance case, a legal
    hold) via resolve_erasure_request, same posture as ComplianceCase.
    Blocked outright while the account is under legal hold - fail fast
    rather than let a request be opened that resolve_erasure_request can
    never actually complete."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise ValueError(f"No such account: {account_id!r}")
    if account.legal_hold:
        raise AccountUnderLegalHoldError(
            f"Account {account_id} is under legal hold ({account.legal_hold_reference or 'no reference'}) "
            "and cannot have an erasure request opened against it."
        )

    request = ErasureRequest(account_id=account_id, requested_by=requested_by, notes=notes)
    db.add(request)
    db.commit()
    db.refresh(request)
    log_event(
        db, actor=requested_by, action="retention.erasure_request_created",
        target=f"erasure_request:{request.id}", account_id=account_id,
        after={"status": request.status.value, "notes": notes},
    )
    publish_retention_erasure_requested(account_id, request_id=request.id)
    return request


def list_erasure_requests(db: Session, *, account_id: str | None = None) -> list[ErasureRequest]:
    """Staff-facing queue (account_id=None) or a customer's own history
    (account_id set) - same optional-filter shape as
    app.compliance.service.list_all_cases."""
    query = db.query(ErasureRequest)
    if account_id is not None:
        query = query.filter(ErasureRequest.account_id == account_id)
    return query.order_by(ErasureRequest.created_at.desc()).all()


def resolve_erasure_request(
    db: Session, request_id: str, *, status: ErasureRequestStatus, resolution_notes: str | None, actor: str
) -> ErasureRequest:
    """Staff-only. Marking COMPLETED actually runs erase_account_data (real
    gap fix - this used to only be staff ATTESTING that deletion happened
    through unspecified "domain tools" that never actually existed).
    Raises AccountUnderLegalHoldError if the account is under an active
    legal hold - callers should surface that as a real error, not a
    silent no-op resolution. REJECTED never deletes anything, matching a
    DSAR that was legitimately refused (e.g. still-open compliance case,
    retained billing/tax evidence). Only a PENDING request can be
    resolved - there is no re-resolving an already-decided request."""
    if status == ErasureRequestStatus.PENDING:
        raise ValueError("resolve_erasure_request cannot set status back to PENDING")
    request = db.query(ErasureRequest).filter(ErasureRequest.id == request_id).first()
    if request is None:
        raise ErasureRequestNotFoundError(f"No such erasure request: {request_id!r}")
    if request.status != ErasureRequestStatus.PENDING:
        raise ErasureRequestNotPendingError(
            f"Erasure request {request_id} is already {request.status.value}, cannot resolve again."
        )
    if status == ErasureRequestStatus.COMPLETED:
        erase_account_data(db, request.account_id, actor=actor)
    before_status = request.status
    request.status = status
    request.resolved_by = actor
    request.resolution_notes = resolution_notes
    request.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    log_event(
        db, actor=actor, action="retention.erasure_request_resolved", target=f"erasure_request:{request.id}",
        account_id=request.account_id,
        before={"status": before_status.value}, after={"status": status.value, "resolution_notes": resolution_notes},
    )
    return request
