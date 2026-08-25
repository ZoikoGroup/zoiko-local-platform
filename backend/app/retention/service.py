from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.events.service import (
    publish_retention_erasure_requested,
    publish_retention_policy_set,
    publish_retention_recording_purged,
)
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.storage.s3 import StorageError, delete_object
from app.integrations.telecom import twilio as telecom
from app.integrations.telecom.twilio import TelecomError
from app.media.models import CallRecord, VideoSession, Voicemail
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


def _purge_voicemails(db: Session, now: datetime) -> tuple[int, int]:
    purged = failed = 0
    for vm in db.query(Voicemail).filter(Voicemail.recording_url.isnot(None)).all():
        retention_days = get_retention_days(db, vm.account_id, ArtifactType.VOICEMAIL)
        if vm.created_at >= now - timedelta(days=retention_days):
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


def _purge_call_recordings(db: Session, now: datetime) -> tuple[int, int]:
    purged = failed = 0
    calls = (
        db.query(CallRecord)
        .filter(CallRecord.recording_url.isnot(None), CallRecord.account_id.isnot(None))
        .all()
    )
    for call in calls:
        retention_days = get_retention_days(db, call.account_id, ArtifactType.CALL_RECORDING)
        if call.created_at >= now - timedelta(days=retention_days):
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


def _purge_video_recordings(db: Session, now: datetime) -> tuple[int, int]:
    purged = failed = 0
    query = db.query(VideoSession).filter(
        VideoSession.recording_url.isnot(None), VideoSession.recording_url != RECORDING_FAILED_MARKER,
    )
    for session in query.all():
        retention_days = get_retention_days(db, session.account_id, ArtifactType.VIDEO_RECORDING)
        reference_time = session.ended_at or session.started_at or session.created_at
        if reference_time >= now - timedelta(days=retention_days):
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


class LegalHoldActiveError(Exception):
    """Raised when an erasure request is created (or resolved to COMPLETED)
    against an account with accounts.legal_hold set - an account under
    litigation/investigation hold must not have its data erasure-requested
    away. This is the actual enforcement this whole feature exists for."""


class ErasureRequestNotFoundError(Exception):
    """Raised when an erasure request id doesn't exist."""


class ErasureRequestNotPendingError(Exception):
    """Raised when trying to resolve an erasure request that isn't
    currently PENDING - a request can only be resolved once."""


def create_erasure_request(
    db: Session, account_id: str, *, requested_by: str, notes: str | None = None
) -> ErasureRequest:
    """Customer (or staff, on a customer's behalf) asks that this account's
    data be erased. Blocked outright while the account is under legal hold
    (see LegalHoldActiveError) - a real litigation/investigation hold must
    never be quietly bypassed by a self-service deletion request."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise ValueError(f"No such account: {account_id!r}")
    if account.legal_hold:
        raise LegalHoldActiveError(
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


def list_erasure_requests(db: Session, *, status: ErasureRequestStatus | None = None) -> list[ErasureRequest]:
    """Staff-facing list, across every account - same posture as
    compliance's list_all_cases (listing isn't the sensitive action here,
    resolving is - see resolve_erasure_request)."""
    query = db.query(ErasureRequest)
    if status is not None:
        query = query.filter(ErasureRequest.status == status)
    return query.order_by(ErasureRequest.created_at.desc()).all()


def list_erasure_requests_for_account(db: Session, account_id: str) -> list[ErasureRequest]:
    """Customer-facing - own account only."""
    return (
        db.query(ErasureRequest)
        .filter(ErasureRequest.account_id == account_id)
        .order_by(ErasureRequest.created_at.desc())
        .all()
    )


def resolve_erasure_request(
    db: Session, request_id: str, *, status: ErasureRequestStatus, resolved_by: str,
    resolution_notes: str | None = None,
) -> ErasureRequest:
    """Marks a PENDING erasure request COMPLETED or REJECTED. Only a
    PENDING request can be resolved (see ErasureRequestNotPendingError) -
    there is no re-resolving an already-decided request.

    IMPORTANT SCOPE BOUNDARY: marking a request COMPLETED does NOT trigger
    any automated PII-scrubbing/data-deletion across the schema (numbers,
    call records, billing, etc.) - it only records that a human resolved
    the request through whatever real deletion process they used outside
    this system. Building that automated deletion pipeline is a separate,
    much larger piece of work than tracking the request lifecycle, same
    scoping boundary this codebase draws elsewhere (see e.g. usage.service.
    record_usage_event's ai_receptionist_minutes docstring: "meters usage
    only, does not enforce billing" - this is that same kind of narrow,
    explicitly-documented scope).

    Re-checks legal_hold at resolution time (not just at creation time) for
    a transition to COMPLETED - a hold could have been placed after the
    request was submitted. REJECTED is always allowed regardless of hold
    status, since rejecting an erasure request never destroys data."""
    request = db.query(ErasureRequest).filter(ErasureRequest.id == request_id).first()
    if request is None:
        raise ErasureRequestNotFoundError(f"No such erasure request: {request_id!r}")
    if request.status != ErasureRequestStatus.PENDING:
        raise ErasureRequestNotPendingError(
            f"Erasure request {request_id} is already {request.status.value}, cannot resolve again."
        )

    if status == ErasureRequestStatus.COMPLETED:
        account = db.query(Account).filter(Account.id == request.account_id).first()
        if account is not None and account.legal_hold:
            raise LegalHoldActiveError(
                f"Account {request.account_id} is under legal hold "
                f"({account.legal_hold_reference or 'no reference'}) and this erasure request cannot be "
                "completed while the hold is active."
            )

    before = {"status": request.status.value}
    request.status = status
    request.resolved_by = resolved_by
    request.resolution_notes = resolution_notes
    request.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(request)
    log_event(
        db, actor=resolved_by, action="retention.erasure_request_resolved",
        target=f"erasure_request:{request.id}", account_id=request.account_id,
        before=before, after={"status": status.value, "resolution_notes": resolution_notes},
    )
    return request
