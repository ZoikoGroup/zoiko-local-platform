"""Roadmap doc §6 "Atomicity law: two customers must never be able to
reserve or purchase the same number. This must be verified by load and
chaos testing before launch" - also listed in §12 as an Automatic No-Go
trigger. reserve_number's SELECT...FOR UPDATE + unique constraint on e164
(see numbering/numbers/service.py) looked sound by inspection, but nothing
in this suite actually fired concurrent requests at it - the rest of this
test suite's client/db_session fixtures share ONE connection/transaction
per test (see conftest.py's db_session fixture), which is exactly wrong
for a concurrency test: SQLAlchemy sessions aren't thread-safe for
concurrent use, and one shared uncommitted transaction can't exhibit a
real cross-transaction row-lock race at all.

This test instead uses real, independent SessionLocal() connections (one
per thread) against the real database, calling reserve_number directly
(not through the HTTP client) so the ThreadPoolExecutor workers are
genuinely separate DB transactions - the actual scenario the doc's
atomicity law is about. Cleans up its own rows explicitly at the end
since it deliberately doesn't use the rollback-at-teardown db_session
fixture.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.core.database import SessionLocal
from app.numbering.identity.models import Account, AccountType
from app.numbering.numbers.models import PhoneNumber
from app.numbering.numbers.service import NumberConflictError, reserve_number


def _make_account(db, name: str) -> str:
    account = Account(name=name, account_type=AccountType.BUSINESS)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account.id


def test_concurrent_reservations_of_the_same_number_only_one_succeeds():
    e164 = f"+1999{uuid.uuid4().int % 10_000_000:07d}"
    setup_db = SessionLocal()
    account_ids: list[str] = []
    try:
        account_ids = [_make_account(setup_db, f"Concurrency Test {i}") for i in range(8)]
    finally:
        setup_db.close()

    def _attempt(account_id: str):
        db = SessionLocal()
        try:
            reserve_number(db, account_id, e164, country="US")
            return "ok"
        except NumberConflictError:
            return "conflict"
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=len(account_ids)) as executor:
            futures = [executor.submit(_attempt, account_id) for account_id in account_ids]
            results = [future.result() for future in as_completed(futures)]

        assert results.count("ok") == 1, (
            f"expected exactly one reservation to succeed under real concurrency, got: {results}"
        )
        assert results.count("conflict") == len(account_ids) - 1
    finally:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).delete()
            cleanup_db.query(Account).filter(Account.id.in_(account_ids)).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()
