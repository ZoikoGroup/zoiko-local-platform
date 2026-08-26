"""Real bug fix: billing_service.is_first_number_included's count-then-act
check (backend/app/billing/service.py) had no lock on anything account-
scoped - only reserve_number's own SELECT...FOR UPDATE on the individual
PhoneNumber row being purchased, which protects a DIFFERENT race (two
accounts racing for the same e164, see test_number_reservation_
concurrency.py). Two concurrent checkouts for two DIFFERENT e164s on the
SAME account could both read included_count < seat_count as true before
either committed, and both take the zero-surcharge "included" path -
granting two free numbers instead of one.

Same real-concurrency approach as test_number_reservation_concurrency.py:
independent SessionLocal() connections (one per thread) against the real
database, calling create_number_purchase_checkout_session directly so the
ThreadPoolExecutor workers are genuinely separate DB transactions - a
single shared db_session fixture can't exhibit a real cross-transaction
row-lock race at all.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from app.billing import service as billing_service
from app.consent.models import ConsentType
from app.consent.service import grant_consent
from app.core.database import SessionLocal
from app.numbering.identity.models import Account, AccountType, User
from app.numbering.numbers.models import PhoneNumber
from app.numbering.numbers.service import create_number_purchase_checkout_session, reserve_number


def test_concurrent_checkouts_for_two_numbers_on_the_same_account_only_one_is_free(monkeypatch):
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": f"PN_fake_{e164}", "phone_number": e164, "capabilities": {}},
    )

    suffix = uuid.uuid4().int % 10_000_000
    e164_a = f"+1999{suffix:07d}"
    e164_b = f"+1998{suffix:07d}"

    setup_db = SessionLocal()
    account_id = None
    try:
        account = Account(name="First Number Free Concurrency Co", account_type=AccountType.BUSINESS)
        setup_db.add(account)
        setup_db.commit()
        setup_db.refresh(account)
        account_id = account.id

        setup_db.add(User(account_id=account_id, email=f"concurrency-{suffix}@example.com"))
        setup_db.commit()

        grant_consent(setup_db, account_id, ConsentType.EMERGENCY_CALLING_ACKNOWLEDGED)
        billing_service.change_plan(setup_db, account_id, "starter", actor="test-actor")

        reserve_number(setup_db, account_id, e164_a, country="US")
        reserve_number(setup_db, account_id, e164_b, country="US")
    finally:
        setup_db.close()

    def _attempt(e164: str):
        db = SessionLocal()
        try:
            result = create_number_purchase_checkout_session(db, account_id, e164)
            return result["included"]
        finally:
            db.close()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(_attempt, e164) for e164 in (e164_a, e164_b)]
            results = [future.result() for future in as_completed(futures)]

        assert results.count(True) == 1, (
            f"expected exactly one of the two concurrent purchases to be free under real concurrency, got: {results}"
        )
        assert results.count(False) == 1
    finally:
        cleanup_db = SessionLocal()
        try:
            cleanup_db.query(PhoneNumber).filter(PhoneNumber.e164.in_([e164_a, e164_b])).delete(synchronize_session=False)
            cleanup_db.query(User).filter(User.account_id == account_id).delete(synchronize_session=False)
            cleanup_db.query(Account).filter(Account.id == account_id).delete(synchronize_session=False)
            cleanup_db.commit()
        finally:
            cleanup_db.close()
