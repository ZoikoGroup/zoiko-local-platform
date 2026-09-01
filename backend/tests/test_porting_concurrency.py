"""porting.service.submit_porting_request's pg_advisory_xact_lock keyed on
the phone number (see that function's own comment) is meant to guarantee
two concurrent submissions for the SAME phone number can never both
succeed - a real gap found in a correctness audit (there was no locking or
unique-constraint backstop at all; two brand-new PortingRequest rows have
no existing row either request could lock the way numbering.reserve_number
does). Looked sound by inspection; this verifies it under real concurrency
the same way test_number_reservation_concurrency.py verifies reserve_
number's atomicity law - independent SessionLocal() connections (one per
thread), not the shared single-transaction db_session fixture, since that
can't exhibit a real cross-transaction lock race at all.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import patch

from app.core.database import SessionLocal
from app.numbering.identity.models import Account, AccountType, User, UserRole
from app.porting.models import PortingRequest
from app.porting.service import PortingRequestConflictError, submit_porting_request


def _make_account_and_user(db, name: str) -> tuple[str, str]:
    account = Account(name=name, account_type=AccountType.BUSINESS)
    db.add(account)
    db.commit()
    db.refresh(account)
    user = User(
        account_id=account.id, email=f"porting-race-{uuid.uuid4()}@example.com",
        role=UserRole.OWNER, hashed_password="x",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return account.id, user.id


def test_concurrent_porting_requests_for_the_same_number_only_one_succeeds():
    phone_number = f"+4420{uuid.uuid4().int % 100_000_000:08d}"
    setup_db = SessionLocal()
    accounts: list[tuple[str, str]] = []
    try:
        accounts = [_make_account_and_user(setup_db, f"Porting Concurrency Test {i}") for i in range(8)]
    finally:
        setup_db.close()

    def _attempt(account_id: str, user_id: str):
        db = SessionLocal()
        try:
            submit_porting_request(
                db, account_id=account_id, requested_by_user_id=user_id, phone_number=phone_number,
                country="GB", current_carrier="Old Carrier Ltd", carrier_account_number="OC-12345",
                billing_name="Porting Concurrency Test Co", billing_address="1 Old Street, London",
            )
            return "ok"
        except PortingRequestConflictError:
            return "conflict"
        finally:
            db.close()

    try:
        # Real email this would otherwise try to send on success - same
        # settings.resend_api_key="" stub test_porting.py's own tests use to
        # avoid a real network call, applied here as a context manager since
        # this test doesn't use pytest's monkeypatch fixture.
        with patch("app.core.config.settings.resend_api_key", ""):
            with ThreadPoolExecutor(max_workers=len(accounts)) as executor:
                futures = [executor.submit(_attempt, account_id, user_id) for account_id, user_id in accounts]
                results = [future.result() for future in as_completed(futures)]

        assert results.count("ok") == 1, (
            f"expected exactly one porting request to succeed under real concurrency for the same "
            f"phone number, got: {results}"
        )
        assert results.count("conflict") == len(accounts) - 1

        verify_db = SessionLocal()
        try:
            rows = verify_db.query(PortingRequest).filter(PortingRequest.phone_number == phone_number).all()
            assert len(rows) == 1, "no duplicate PortingRequest rows should exist for one phone number"
        finally:
            verify_db.close()
    finally:
        cleanup_db = SessionLocal()
        try:
            account_ids = [a for a, _ in accounts]
            cleanup_db.query(PortingRequest).filter(PortingRequest.phone_number == phone_number).delete()
            cleanup_db.query(User).filter(User.account_id.in_(account_ids)).delete(synchronize_session=False)
            cleanup_db.query(Account).filter(Account.id.in_(account_ids)).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()
