"""queues.service.pull_next_caller's `with_for_update(skip_locked=True)` +
`agent_user_id` reservation marker (see that function's own docstring) is
meant to guarantee two agents concurrently hitting POST /queues/{id}/
pull-next can never both be dispatched a real outbound call for the SAME
single waiting caller - a real bug found in a correctness audit (the
previous version had no lock at all). Looked sound by inspection; this
verifies it under real concurrency the same way test_number_reservation_
concurrency.py verifies reserve_number's atomicity law - independent
SessionLocal() connections (one per thread), not the shared single-
transaction db_session fixture, since that can't exhibit a real
cross-transaction row-lock race at all.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from app.core.database import SessionLocal
from app.numbering.identity.models import Account, AccountType, User, UserRole
from app.queues.models import AgentPresence, CallQueue, QueueCallLog, QueueMember
from app.queues.service import NoWaitingCallerError, pull_next_caller


def _setup(db) -> tuple[str, str, list[str]]:
    account = Account(name="Queue Concurrency Test Co", account_type=AccountType.BUSINESS)
    db.add(account)
    db.commit()
    db.refresh(account)

    queue = CallQueue(account_id=account.id, name="Concurrency Test Queue")
    db.add(queue)
    db.commit()
    db.refresh(queue)

    agent_ids = []
    for i in range(6):
        agent = User(
            account_id=account.id, email=f"pull-race-agent-{i}-{uuid.uuid4()}@example.com",
            phone_number=f"+1555000{i:04d}", role=UserRole.OWNER, hashed_password="x",
        )
        db.add(agent)
        db.commit()
        db.refresh(agent)
        db.add(QueueMember(queue_id=queue.id, user_id=agent.id))
        agent_ids.append(agent.id)
    db.commit()

    call_sid = f"CAconcurrency{uuid.uuid4().hex[:16]}"
    log = QueueCallLog(queue_id=queue.id, call_sid=call_sid, caller_number="+15551110000")
    db.add(log)
    db.commit()

    return account.id, queue.id, agent_ids


def test_concurrent_pulls_of_the_same_waiting_caller_only_one_succeeds():
    setup_db = SessionLocal()
    try:
        account_id, queue_id, agent_ids = _setup(setup_db)
    finally:
        setup_db.close()

    # Real network call this function places - stubbed the same way
    # test_queues.py's test_full_queue_lifecycle_enqueue_pull_answer_wrapup
    # stubs it, just via unittest.mock.patch instead of the monkeypatch
    # fixture (this test doesn't use pytest's client/db_session fixtures at
    # all, so there's no monkeypatch teardown hook to lean on - `with
    # patch(...)` is process-global for its duration regardless of thread,
    # same effect, self-contained cleanup).
    def _attempt(agent_id: str):
        db = SessionLocal()
        try:
            queue = db.query(CallQueue).filter(CallQueue.id == queue_id).first()
            agent = db.query(User).filter(User.id == agent_id).first()
            try:
                result = pull_next_caller(db, queue, agent, base_url="http://testserver/")
                return ("ok", result)
            except NoWaitingCallerError:
                return ("no_caller", None)
        finally:
            db.close()

    try:
        with patch(
            "app.queues.service.telecom.place_call",
            lambda **kwargs: {"sid": f"CAagent{uuid.uuid4().hex[:16]}", "status": "queued"},
        ):
            with ThreadPoolExecutor(max_workers=len(agent_ids)) as executor:
                futures = [executor.submit(_attempt, agent_id) for agent_id in agent_ids]
                results = [future.result() for future in as_completed(futures)]

        outcomes = [r[0] for r in results]
        assert outcomes.count("ok") == 1, (
            f"expected exactly one agent to be dispatched the single waiting caller under real "
            f"concurrency, got: {outcomes}"
        )
        assert outcomes.count("no_caller") == len(agent_ids) - 1

        # The reservation must have stuck: exactly one QueueCallLog row for
        # this queue ended up with agent_user_id set, and it's whichever
        # agent's attempt actually returned "ok" above - not a second,
        # different row silently created, and not left un-reserved.
        verify_db = SessionLocal()
        try:
            rows = verify_db.query(QueueCallLog).filter(QueueCallLog.queue_id == queue_id).all()
            assert len(rows) == 1, "no duplicate QueueCallLog rows should exist for one caller"
            winner_agent_id = next(aid for aid, r in zip(agent_ids, results) if r[0] == "ok")
            assert rows[0].agent_user_id == winner_agent_id
        finally:
            verify_db.close()
    finally:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(QueueCallLog).filter(QueueCallLog.queue_id == queue_id).delete()
            cleanup_db.query(QueueMember).filter(QueueMember.queue_id == queue_id).delete()
            cleanup_db.query(AgentPresence).filter(AgentPresence.user_id.in_(agent_ids)).delete(
                synchronize_session=False
            )
            cleanup_db.query(User).filter(User.id.in_(agent_ids)).delete(synchronize_session=False)
            cleanup_db.query(CallQueue).filter(CallQueue.id == queue_id).delete()
            cleanup_db.query(Account).filter(Account.id == account_id).delete()
            cleanup_db.commit()
        finally:
            cleanup_db.close()
