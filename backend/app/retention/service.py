from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.events.service import publish_retention_policy_set, publish_retention_recording_purged
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.storage.s3 import StorageError, delete_object
from app.integrations.telecom import twilio as telecom
from app.integrations.telecom.twilio import TelecomError
from app.media.models import CallRecord, VideoSession, Voicemail
from app.retention.models import ArtifactType, RetentionPolicy

# Safety-net fallback when no policy row exists at all (shouldn't normally
# happen once the migration seeds global defaults, but never leave retention
# undefined - roadmap doc requires it be "retained by policy", not forever).
DEFAULT_RETENTION_DAYS = 90

PURGED_MARKER = "[deleted - retention policy]"


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
    for session in db.query(VideoSession).filter(VideoSession.recording_url.isnot(None)).all():
        retention_days = get_retention_days(db, session.account_id, ArtifactType.VIDEO_RECORDING)
        reference_time = session.ended_at or session.started_at or session.created_at
        if reference_time >= now - timedelta(days=retention_days):
            continue
        try:
            delete_object(f"recordings/{session.room_name}.mp4")
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
