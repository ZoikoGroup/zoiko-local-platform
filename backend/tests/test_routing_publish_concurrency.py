"""routing.service.publish_flow/rollback_flow's _lock_flow_versions
(with_for_update() over every CallFlowVersion row for a call_flow_id) is
meant to guarantee CallFlowVersion's own documented invariant - "exactly
one PUBLISHED version per call_flow_id" - holds even under concurrent
publishes, per that helper's own docstring: without it, two concurrent
publish_flow calls for the same flow could both read the same stale draft/
live state and each independently compute the same next version number,
leaving two DRAFT rows with colliding version numbers and silently losing
one admin's edits. A real gap found in a correctness audit (there was no
locking at all). Looked sound by inspection; this verifies it under real
concurrency the same way test_number_reservation_concurrency.py verifies
reserve_number's atomicity law - independent SessionLocal() connections
(one per thread), not the shared single-transaction db_session fixture,
since a blocking with_for_update() lock can't be exercised meaningfully
inside one shared, never-committing transaction.

Unlike reserve_number/submit_porting_request (where only one of N
concurrent callers should succeed and the rest get a conflict error),
with_for_update() here is a plain blocking lock, not skip_locked - so every
concurrent publish_flow call is expected to eventually succeed, serialized
one at a time, each publishing whatever draft it sees once it acquires the
lock (a copy of the previously-published one, so always valid). The
invariant under test is that serialization actually happens cleanly: no
crash, no duplicate version numbers, and exactly one PUBLISHED row at the
end.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from app.core.database import SessionLocal
from app.numbering.identity.models import Account, AccountType, User, UserRole
from app.routing.models import CallFlow, CallFlowVersion, CallFlowVersionStatus
from app.routing.service import create_flow, publish_flow, save_draft

_VALID_NODES = [{"id": "vm_only", "type": "voicemail"}]


def test_concurrent_publishes_of_the_same_flow_never_collide_on_version_number():
    setup_db = SessionLocal()
    try:
        account = Account(name="Routing Concurrency Test Co", account_type=AccountType.BUSINESS)
        setup_db.add(account)
        setup_db.commit()
        setup_db.refresh(account)
        user = User(
            account_id=account.id, email=f"publish-race-{uuid.uuid4()}@example.com",
            role=UserRole.OWNER, hashed_password="x",
        )
        setup_db.add(user)
        setup_db.commit()
        setup_db.refresh(user)

        flow = create_flow(setup_db, account.id, "Concurrency Test Flow", actor_id=user.id)
        save_draft(setup_db, account.id, flow.id, "vm_only", _VALID_NODES, actor_id=user.id)
        account_id, user_id, flow_id = account.id, user.id, flow.id
    finally:
        setup_db.close()

    worker_count = 6

    def _attempt():
        db = SessionLocal()
        try:
            published, errors, version = publish_flow(db, account_id, flow_id, actor_id=user_id)
            return (published, errors)
        finally:
            db.close()

    try:
        with patch("app.core.config.settings.resend_api_key", ""):
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                futures = [executor.submit(_attempt) for _ in range(worker_count)]
                results = [future.result() for future in as_completed(futures)]

        assert all(published for published, _ in results), (
            f"every concurrent publish should succeed once serialized by the lock "
            f"(each sees a fresh, valid, copied-forward draft), got: {results}"
        )

        verify_db = SessionLocal()
        try:
            versions = (
                verify_db.query(CallFlowVersion).filter(CallFlowVersion.call_flow_id == flow_id).all()
            )
            version_numbers = [v.version for v in versions]
            assert len(version_numbers) == len(set(version_numbers)), (
                f"no two CallFlowVersion rows for one flow should share a version number, got: "
                f"{sorted(version_numbers)}"
            )
            # original draft (v1) + one new draft created per successful publish
            assert len(versions) == worker_count + 1

            published_rows = [v for v in versions if v.status == CallFlowVersionStatus.PUBLISHED]
            assert len(published_rows) == 1, (
                f"exactly one PUBLISHED version must exist per call_flow_id (this table's own "
                f"documented invariant), found: {len(published_rows)}"
            )
        finally:
            verify_db.close()
    finally:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(CallFlowVersion).filter(CallFlowVersion.call_flow_id == flow_id).delete()
            cleanup_db.query(CallFlow).filter(CallFlow.id == flow_id).delete()
            cleanup_db.query(User).filter(User.id == user_id).delete()
            cleanup_db.query(Account).filter(Account.id == account_id).delete()
            cleanup_db.commit()
        finally:
            cleanup_db.close()
