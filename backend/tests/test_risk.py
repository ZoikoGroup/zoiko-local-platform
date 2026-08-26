from datetime import datetime, timezone

from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import CallerIdentity, CallerIdentityStatus, PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Risk Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _clear_billing_trial_gate(db_session, account_id: str) -> None:
    """app.core.deps.require_paid_or_read_only blocks write actions
    (placing a call, buying a number, opening a compliance case) for a
    TRIALING-status account. Deliberately a direct DB status flip, not a
    real PUT /billing/subscription/plan call: that endpoint's service
    function has a genuine side effect beyond billing - it also promotes
    AccountRiskState (see risk.service's plan-change hook) - and this
    file's tests need to control AccountRiskState (TRIAL_LOW vs
    PAID_NORMAL vs TRIAL_VERIFIED) independently and precisely via their
    own explicit setup (_promote_to_paid_normal below) or assertions, not
    have it silently pre-empted by clearing the billing gate."""
    from app.billing.models import SubscriptionStatus
    from app.billing.service import get_or_create_subscription

    sub = get_or_create_subscription(db_session, account_id)
    sub.status = SubscriptionStatus.ACTIVE
    db_session.commit()


def _create_staff_and_login(client, db_session, email: str, role):
    from app.staff import service as staff_service

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _active_number(db_session, account_id: str, e164: str) -> PhoneNumber:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    # Real purchases auto-create a VERIFIED CallerIdentity (see
    # assert_caller_id_authorized) - this helper bypasses purchase_number
    # entirely, so it must create one itself or every outbound call in these
    # tests gets rejected as an unauthorized caller ID.
    db_session.add(CallerIdentity(
        phone_number_id=number.id, account_id=account_id, status=CallerIdentityStatus.VERIFIED,
        verification_source="test-fixture", verified_at=datetime.now(timezone.utc),
    ))
    db_session.commit()
    return number


def _promote_to_paid_normal(db_session, account_id: str) -> None:
    """Every fresh signup starts at AccountRiskState.TRIAL_LOW (see
    Account.risk_state's default), whose concurrent-call limit is 1 - see
    MAX_CONCURRENT_CALLS_BY_RISK_STATE. The stubbed telecom.place_call used
    throughout this file returns a non-terminal status ("queued") and never
    advances (no real Twilio status callback ever fires in a test), so
    without this, tests that place several outbound calls back-to-back to
    exercise velocity/dispersion limits would instead hit the unrelated
    concurrent-call limit on their second call. Real-world sequential
    calling wouldn't hit this the same way (each call's real status
    callback typically lands before the next one is placed) - this promotes
    the account so these tests isolate the limit they're actually about."""
    from app.numbering.identity.models import Account
    from app.risk.models import AccountRiskState

    account = db_session.query(Account).filter(Account.id == account_id).first()
    account.risk_state = AccountRiskState.PAID_NORMAL
    db_session.commit()


def test_customer_cannot_manage_blocked_destinations(client):
    token = _signup_and_login(client, "riskcustomer@example.com")
    response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_support_staff_cannot_add_blocked_destination(client, db_session):
    from app.staff.models import PlatformStaffRole

    staff_token = _create_staff_and_login(client, db_session, "risksupport1@zoikolocal.com", PlatformStaffRole.SUPPORT)
    response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403


def test_super_admin_can_add_and_support_staff_can_list(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskadmin1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    create_response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201

    support_token = _create_staff_and_login(
        client, db_session, "risksupport2@zoikolocal.com", PlatformStaffRole.SUPPORT
    )
    list_response = client.get(
        "/risk/blocked-destinations", headers={"Authorization": f"Bearer {support_token}"}
    )
    assert list_response.status_code == 200
    assert any(r["prefix"] == "+1900" for r in list_response.json())


def test_outbound_call_to_blocked_destination_is_rejected(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskadmin2@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    token = _signup_and_login(client, "riskblocked@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550009999")

    response = client.post(
        "/media/voice/outbound",
        json={"to": "+19005551234", "from": "+15550009999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "blocked destination" in response.json()["detail"].lower()


def test_outbound_call_velocity_limit_is_enforced(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAvelocity", "status": "completed", "to": kwargs["to"], "from": kwargs["from_"]},
    )

    token = _signup_and_login(client, "riskvelocity@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550008888")
    _promote_to_paid_normal(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    from app.risk.service import MAX_OUTBOUND_CALLS_PER_WINDOW

    for _ in range(MAX_OUTBOUND_CALLS_PER_WINDOW):
        response = client.post(
            "/media/voice/outbound",
            json={"to": "+15551230000", "from": "+15550008888"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    over_limit_response = client.post(
        "/media/voice/outbound",
        json={"to": "+15551230000", "from": "+15550008888"},
        headers=headers,
    )
    assert over_limit_response.status_code == 429


def _real_accounts(db_session, n: int, prefix: str) -> list[str]:
    """CallRecord.account_id is a real FK into accounts - unlike a plain
    string, Postgres will reject a fake id, so tests that write CallRecord
    rows directly need real Account rows behind them."""
    from app.numbering.identity.models import Account, AccountType

    ids = []
    for i in range(n):
        account = Account(name=f"{prefix}-{i}", account_type=AccountType.BUSINESS)
        db_session.add(account)
        db_session.flush()
        ids.append(account.id)
    return ids


def test_is_suspected_spam_caller_below_threshold(db_session):
    """Roadmap 'AI-driven fraud/spam signals': a real customer calling one
    or two different businesses is normal traffic, not a signal."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import is_suspected_spam_caller

    for account_id in _real_accounts(db_session, 2, "spamtest-below"):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND,
                from_number="+15559990001", to_number="+15550000001", status="ringing",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990001") is False


def test_is_suspected_spam_caller_at_threshold(db_session):
    """The same number fanning out across INBOUND_SPAM_ACCOUNT_THRESHOLD
    distinct accounts in the window IS the signal - no single account's own
    call history could ever surface this pattern on its own."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import INBOUND_SPAM_ACCOUNT_THRESHOLD, is_suspected_spam_caller

    for account_id in _real_accounts(db_session, INBOUND_SPAM_ACCOUNT_THRESHOLD, "spamtest-at"):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND,
                from_number="+15559990002", to_number="+15550000002", status="ringing",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990002") is True


def test_is_suspected_spam_caller_ignores_repeat_calls_to_the_same_account(db_session):
    """Calling the SAME business 5 times isn't fan-out - it's one distinct
    account, however many times they called."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import is_suspected_spam_caller

    account_id = _real_accounts(db_session, 1, "spamtest-loyal")[0]
    for _ in range(5):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND,
                from_number="+15559990003", to_number="+15550000003", status="ringing",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990003") is False


def test_is_suspected_spam_caller_ignores_outbound_calls(db_session):
    """The velocity signal is about calls landing on many businesses, not
    an account placing many outbound calls (that's the separate
    assert_outbound_velocity_ok gate above)."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import INBOUND_SPAM_ACCOUNT_THRESHOLD, is_suspected_spam_caller

    for account_id in _real_accounts(db_session, INBOUND_SPAM_ACCOUNT_THRESHOLD, "spamtest-outbound"):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.OUTBOUND,
                from_number="+15550000004", to_number="+15559990004", status="queued",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990004") is False


# --- Geographic dispersion (IRSF-style) and spend-limit controls ---
# (Commercial Billing Operating Standard doc's "real-time fraud/toll-abuse
# spend controls")


def test_outbound_call_geographic_dispersion_limit_is_enforced(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAdispersion", "status": "completed", "to": kwargs["to"], "from": kwargs["from_"]},
    )

    token = _signup_and_login(client, "riskdispersion@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550007777")
    _promote_to_paid_normal(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    from app.risk.service import GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD

    # Real-shaped E.164 numbers matching phonenumbers' own region metadata
    # for a distinct country each (assert_geographic_dispersion_ok now
    # parses the actual destination country via the phonenumbers library,
    # not a coarse leading-digit prefix - see app/risk/service.py's
    # _country_for_e164).
    distinct_country_numbers = [
        "+14155552671",  # US
        "+442071838750",  # GB
        "+33142685300",  # FR
        "+4930123456",  # DE
        "+81312345678",  # JP
    ]
    for destination in distinct_country_numbers[: GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1]:
        response = client.post(
            "/media/voice/outbound",
            json={"to": destination, "from": "+15550007777"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    over_limit_response = client.post(
        "/media/voice/outbound",
        json={"to": distinct_country_numbers[GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1], "from": "+15550007777"},
        headers=headers,
    )
    assert over_limit_response.status_code == 429
    assert "countries" in over_limit_response.json()["detail"].lower()


def test_assert_spend_limit_ok_passes_under_threshold(db_session):
    from app.risk.service import assert_spend_limit_ok

    account_id = _real_accounts(db_session, 1, "spend-under")[0]
    assert_spend_limit_ok(db_session, account_id)  # no usage at all - must not raise


def test_assert_spend_limit_ok_raises_over_threshold(db_session):
    from app.risk.service import MAX_SPEND_CENTS_PER_WINDOW, SpendLimitExceededError, assert_spend_limit_ok
    from app.usage.service import record_usage_event

    account_id = _real_accounts(db_session, 1, "spend-over")[0]

    # Two events whose combined estimated_cost_cents crosses the limit -
    # proves this sums across events in the window, not just checking one.
    # upsert (not a raw insert) since "US" may already have a seeded rate.
    from app.usage.service import upsert_calling_rate

    upsert_calling_rate(db_session, country="US", price_per_minute_cents=MAX_SPEND_CENTS_PER_WINDOW)

    record_usage_event(
        db_session, account_id=account_id, event_type="call_seconds", quantity=61, unit="seconds",
        country_band="US", idempotency_key="spend-over-1",
    )
    record_usage_event(
        db_session, account_id=account_id, event_type="call_seconds", quantity=61, unit="seconds",
        country_band="US", idempotency_key="spend-over-2",
    )

    try:
        assert_spend_limit_ok(db_session, account_id)
        assert False, "expected SpendLimitExceededError"
    except SpendLimitExceededError:
        pass

    from app.risk.models import RiskSignal, RiskSignalType

    signal = (
        db_session.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.signal_type == RiskSignalType.SPEND_LIMIT_EXCEEDED)
        .first()
    )
    assert signal is not None


# --- Data-driven fraud weights (FraudRule) ---


def test_get_signal_weight_falls_back_to_default_with_no_rule(db_session):
    # SPEND_LIMIT_EXCEEDED has no seeded FraudRule row (migration
    # 7a2e5c918bf4 deliberately doesn't seed it - see that migration's
    # docstring), unlike VELOCITY_EXCEEDED/BLOCKED_DESTINATION_ATTEMPT/
    # GEOGRAPHIC_DISPERSION which all do - a genuine "no rule at all" case.
    from app.risk.models import RiskSignalType
    from app.risk.service import _DEFAULT_WEIGHTS, get_signal_weight

    assert get_signal_weight(db_session, RiskSignalType.SPEND_LIMIT_EXCEEDED) == _DEFAULT_WEIGHTS[RiskSignalType.SPEND_LIMIT_EXCEEDED]


def test_get_signal_weight_uses_active_fraud_rule_override(db_session):
    # SPEND_LIMIT_EXCEEDED, not VELOCITY_EXCEEDED - the latter already has
    # a seeded FraudRule row (migration 7a2e5c918bf4), which would collide
    # with this test's own insert on the unique(signal_type) constraint.
    from app.risk.models import FraudRule, RiskSignalType
    from app.risk.service import get_signal_weight

    db_session.add(FraudRule(signal_type=RiskSignalType.SPEND_LIMIT_EXCEEDED, weight=99, is_active=True))
    db_session.commit()

    assert get_signal_weight(db_session, RiskSignalType.SPEND_LIMIT_EXCEEDED) == 99


def test_get_signal_weight_is_nonzero_for_every_risk_signal_type(db_session):
    """Real bug fix: DEVICE_FINGERPRINT_ABUSE, AI_RECEPTIONIST_TRIAL_CAP_
    EXCEEDED, REPEATED_NUMBER_ACQUISITION, CALLER_ID_CHANGE_PATTERN, and
    ACCOUNT_TAKEOVER_INDICATOR were all being recorded by record_risk_signal
    (each has its own real detection function - is_suspected_fingerprint_
    abuse, assert_number_acquisition_velocity_ok, etc.) but were missing
    from _DEFAULT_WEIGHTS entirely, so get_signal_weight silently returned
    0 for all five - they could never move compute_account_risk_score or
    trigger auto-suspend/review, no matter how many fired. This asserts
    every RiskSignalType has a real, positive weight with no FraudRule
    row present (the pure-fallback path)."""
    from app.risk.models import RiskSignalType
    from app.risk.service import get_signal_weight

    for signal_type in RiskSignalType:
        assert get_signal_weight(db_session, signal_type) > 0, signal_type


def test_get_signal_weight_is_zero_for_an_explicitly_deactivated_fraud_rule(db_session):
    """An inactive FraudRule row means staff explicitly silenced this signal
    type - it must contribute 0, not fall back to the built-in default
    (that fallback is only for signal types with no row at all). Otherwise
    staff would have no way to actually turn a noisy signal off."""
    from app.risk.models import FraudRule, RiskSignalType
    from app.risk.service import get_signal_weight

    db_session.add(FraudRule(signal_type=RiskSignalType.SPEND_LIMIT_EXCEEDED, weight=99, is_active=False))
    db_session.commit()

    assert get_signal_weight(db_session, RiskSignalType.SPEND_LIMIT_EXCEEDED) == 0


# --- Fraud case review queue ---


def test_signal_at_review_threshold_opens_a_fraud_case_without_suspending(db_session):
    from app.risk.models import FraudCase, FraudCaseStatus, RiskSignalType
    from app.risk.service import record_risk_signal

    account_id = _real_accounts(db_session, 1, "fraudcase-open")[0]
    # BLOCKED_DESTINATION_ATTEMPT's default weight (40) alone is below both
    # REVIEW_THRESHOLD (70) and AUTO_SUSPEND_THRESHOLD (100); two signals
    # (80) crosses REVIEW_THRESHOLD but not AUTO_SUSPEND_THRESHOLD.
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="2")

    case = db_session.query(FraudCase).filter(FraudCase.account_id == account_id).first()
    assert case is not None
    assert case.status == FraudCaseStatus.OPEN
    assert case.score_at_open >= 70


def test_repeated_signals_do_not_open_duplicate_open_fraud_cases(db_session):
    from app.risk.models import FraudCase, RiskSignalType
    from app.risk.service import record_risk_signal

    account_id = _real_accounts(db_session, 1, "fraudcase-dedup")[0]
    for _ in range(3):
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="x")

    open_cases = db_session.query(FraudCase).filter(FraudCase.account_id == account_id).all()
    assert len(open_cases) == 1


def test_resolve_fraud_case_requires_super_admin_or_compliance_officer(client, db_session):
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "fraudcase-authz")[0]
    from app.risk.models import FraudCase, FraudCaseStatus

    case = FraudCase(account_id=account_id, score_at_open=70, status=FraudCaseStatus.OPEN)
    db_session.add(case)
    db_session.commit()

    support_token = _create_staff_and_login(client, db_session, "fraudcasesupport@zoikolocal.com", PlatformStaffRole.SUPPORT)
    response = client.post(
        f"/risk/fraud-cases/{case.id}/resolve",
        json={"status": "cleared", "notes": "false positive"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_resolve_fraud_case_succeeds_for_compliance_officer(client, db_session):
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "fraudcase-resolve")[0]
    from app.risk.models import FraudCase, FraudCaseStatus

    case = FraudCase(account_id=account_id, score_at_open=70, status=FraudCaseStatus.OPEN)
    db_session.add(case)
    db_session.commit()

    officer_token = _create_staff_and_login(
        client, db_session, "fraudcaseofficer@zoikolocal.com", PlatformStaffRole.COMPLIANCE_OFFICER
    )
    response = client.post(
        f"/risk/fraud-cases/{case.id}/resolve",
        json={"status": "confirmed", "notes": "verified abuse pattern"},
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["resolved_by"] is not None
    assert body["resolution_notes"] == "verified abuse pattern"


# --- Device fingerprinting (Architecture doc §5 "Fraud and Risk: device fingerprinting") ---


def test_is_suspected_fingerprint_abuse_below_threshold(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import is_suspected_fingerprint_abuse

    for account_id in _real_accounts(db_session, 2, "fp-below"):
        db_session.add(DeviceFingerprintSighting(fingerprint_hash="fp-below-hash", account_id=account_id))
    db_session.commit()

    assert is_suspected_fingerprint_abuse(db_session, "fp-below-hash") is False


def test_is_suspected_fingerprint_abuse_at_threshold(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD, is_suspected_fingerprint_abuse

    for account_id in _real_accounts(db_session, DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD, "fp-at"):
        db_session.add(DeviceFingerprintSighting(fingerprint_hash="fp-at-hash", account_id=account_id))
    db_session.commit()

    assert is_suspected_fingerprint_abuse(db_session, "fp-at-hash") is True


def test_check_fingerprint_on_signup_is_a_noop_with_no_fingerprint(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import check_fingerprint_on_signup

    account_id = _real_accounts(db_session, 1, "fp-none")[0]
    check_fingerprint_on_signup(db_session, fingerprint_hash=None, account_id=account_id)

    assert db_session.query(DeviceFingerprintSighting).filter(DeviceFingerprintSighting.account_id == account_id).count() == 0


def test_check_fingerprint_on_signup_records_a_sighting(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import check_fingerprint_on_signup

    account_id = _real_accounts(db_session, 1, "fp-record")[0]
    check_fingerprint_on_signup(db_session, fingerprint_hash="fp-record-hash", account_id=account_id)

    sighting = (
        db_session.query(DeviceFingerprintSighting)
        .filter(DeviceFingerprintSighting.account_id == account_id)
        .first()
    )
    assert sighting is not None
    assert sighting.fingerprint_hash == "fp-record-hash"


def test_check_fingerprint_on_signup_raises_a_signal_at_threshold_but_never_blocks(db_session):
    from app.risk.models import RiskSignal, RiskSignalType
    from app.risk.service import DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD, check_fingerprint_on_signup

    accounts = _real_accounts(db_session, DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD, "fp-signal")
    for account_id in accounts:
        # Must not raise - detection only, never a signup gate.
        check_fingerprint_on_signup(db_session, fingerprint_hash="fp-signal-hash", account_id=account_id)

    signal = (
        db_session.query(RiskSignal)
        .filter(RiskSignal.account_id == accounts[-1], RiskSignal.signal_type == RiskSignalType.DEVICE_FINGERPRINT_ABUSE)
        .first()
    )
    assert signal is not None


def test_signup_endpoint_accepts_an_optional_fingerprint_header(client):
    response = client.post(
        "/auth/signup",
        json={
            "account_name": "Fingerprint Header Co", "account_type": "business",
            "email": "fingerprintheader@example.com", "password": "supersecret123",
        },
        headers={"X-Device-Fingerprint": "abc123"},
    )
    assert response.status_code == 201


def test_signup_endpoint_works_without_a_fingerprint_header(client):
    response = client.post(
        "/auth/signup",
        json={
            "account_name": "No Fingerprint Co", "account_type": "business",
            "email": "nofingerprint@example.com", "password": "supersecret123",
        },
    )
    assert response.status_code == 201


def test_check_fingerprint_on_login_is_a_noop_with_no_fingerprint(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import check_fingerprint_on_login

    account_id = _real_accounts(db_session, 1, "fp-login-none")[0]
    check_fingerprint_on_login(db_session, fingerprint_hash=None, account_id=account_id)

    assert db_session.query(DeviceFingerprintSighting).filter(DeviceFingerprintSighting.account_id == account_id).count() == 0


def test_check_fingerprint_on_login_records_a_sighting(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import check_fingerprint_on_login

    account_id = _real_accounts(db_session, 1, "fp-login-record")[0]
    check_fingerprint_on_login(db_session, fingerprint_hash="fp-login-record-hash", account_id=account_id)

    sighting = (
        db_session.query(DeviceFingerprintSighting)
        .filter(DeviceFingerprintSighting.account_id == account_id)
        .first()
    )
    assert sighting is not None
    assert sighting.fingerprint_hash == "fp-login-record-hash"


def test_check_fingerprint_on_login_raises_a_signal_at_threshold_but_never_blocks(db_session):
    from app.risk.models import RiskSignal, RiskSignalType
    from app.risk.service import DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD, check_fingerprint_on_login

    accounts = _real_accounts(db_session, DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD, "fp-login-signal")
    for account_id in accounts:
        # Must not raise - detection only, never a login gate.
        check_fingerprint_on_login(db_session, fingerprint_hash="fp-login-signal-hash", account_id=account_id)

    signal = (
        db_session.query(RiskSignal)
        .filter(RiskSignal.account_id == accounts[-1], RiskSignal.signal_type == RiskSignalType.DEVICE_FINGERPRINT_ABUSE)
        .first()
    )
    assert signal is not None


def test_login_endpoint_accepts_an_optional_fingerprint_header(client):
    client.post(
        "/auth/signup",
        json={
            "account_name": "Login Fingerprint Co", "account_type": "business",
            "email": "loginfingerprint@example.com", "password": "supersecret123",
        },
    )
    response = client.post(
        "/auth/login",
        json={"email": "loginfingerprint@example.com", "password": "supersecret123"},
        headers={"X-Device-Fingerprint": "login-header-hash"},
    )
    assert response.status_code == 200
    assert response.json()["access_token"] is not None


def test_login_endpoint_works_without_a_fingerprint_header(client):
    client.post(
        "/auth/signup",
        json={
            "account_name": "No Login Fingerprint Co", "account_type": "business",
            "email": "nologinfingerprint@example.com", "password": "supersecret123",
        },
    )
    response = client.post(
        "/auth/login",
        json={"email": "nologinfingerprint@example.com", "password": "supersecret123"},
    )
    assert response.status_code == 200


def test_check_fingerprint_on_call_records_a_sighting(db_session):
    from app.risk.models import DeviceFingerprintSighting
    from app.risk.service import check_fingerprint_on_call

    account_id = _real_accounts(db_session, 1, "fp-call-record")[0]
    check_fingerprint_on_call(db_session, fingerprint_hash="fp-call-record-hash", account_id=account_id)

    sighting = (
        db_session.query(DeviceFingerprintSighting)
        .filter(DeviceFingerprintSighting.account_id == account_id)
        .first()
    )
    assert sighting is not None
    assert sighting.fingerprint_hash == "fp-call-record-hash"


def test_outbound_call_endpoint_accepts_an_optional_fingerprint_header(client, db_session, monkeypatch):
    from app.risk.models import DeviceFingerprintSighting

    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAfingerprint", "status": "completed", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    token = _signup_and_login(client, "callfingerprint@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550007000")

    response = client.post(
        "/media/voice/outbound",
        json={"to": "+15551230000", "from": "+15550007000"},
        headers={"Authorization": f"Bearer {token}", "X-Device-Fingerprint": "call-header-hash"},
    )
    assert response.status_code == 200, response.text

    sighting = (
        db_session.query(DeviceFingerprintSighting)
        .filter(DeviceFingerprintSighting.account_id == account_id, DeviceFingerprintSighting.fingerprint_hash == "call-header-hash")
        .first()
    )
    assert sighting is not None


def test_inbound_call_fanning_out_across_accounts_gets_flagged_via_the_real_webhook(client, db_session):
    """End-to-end: the same caller dialing INBOUND_SPAM_ACCOUNT_THRESHOLD
    distinct accounts' numbers through the real /media/voice/incoming
    webhook gets flagged on the call that crosses the threshold - proving
    the signal fires from real call traffic, not just direct service calls."""
    from app.risk.service import INBOUND_SPAM_ACCOUNT_THRESHOLD

    spam_caller = "+15559990005"
    tokens = []
    for i in range(INBOUND_SPAM_ACCOUNT_THRESHOLD):
        token = _signup_and_login(client, f"riskfanout{i}@example.com")
        account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
        _active_number(db_session, account_id, f"+1555000100{i}")
        tokens.append(token)

        url = "http://testserver/media/voice/incoming"
        params = {
            "To": f"+1555000100{i}", "From": spam_caller, "CallSid": f"CAfanout{i}", "CallStatus": "ringing",
        }
        signature = _twilio_signature(url, params)
        response = client.post("/media/voice/incoming", data=params, headers={"X-Twilio-Signature": signature})
        assert response.status_code == 200

    last_account_calls = client.get(
        "/media/voice/calls", headers={"Authorization": f"Bearer {tokens[-1]}"}
    ).json()
    assert len(last_account_calls) == 1
    assert last_account_calls[0]["is_suspected_spam"] is True

    first_account_calls = client.get(
        "/media/voice/calls", headers={"Authorization": f"Bearer {tokens[0]}"}
    ).json()
    assert first_account_calls[0]["is_suspected_spam"] is False


# --- Trial-abuse step-up model (Production Readiness Standard doc) ---


def test_new_account_defaults_to_trial_low(client, db_session):
    from app.numbering.identity.models import Account
    from app.risk.models import AccountRiskState

    token = _signup_and_login(client, "risktrialdefault@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.TRIAL_LOW


def test_concurrent_call_limit_blocks_a_second_in_flight_call_for_a_trial_account(client, db_session, monkeypatch):
    """A brand-new TRIAL_LOW account's concurrent-call limit is 1 - the
    stubbed telecom.place_call below returns a non-terminal ("queued")
    status and never advances, so the first call stays "in flight" for the
    second call's check, exactly like a real second call placed before the
    first one's Twilio status callback has arrived."""
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAconcurrent1", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    token = _signup_and_login(client, "riskconcurrent1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    # Clears the billing-trial gate (a separate concept from this test's
    # TRIAL_LOW risk state - see _clear_billing_trial_gate's docstring)
    # without promoting risk_state away from TRIAL_LOW, which this test
    # depends on for its concurrent-call limit of 1.
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550009090")
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
        "/media/voice/outbound", json={"to": "+15551110000", "from": "+15550009090"}, headers=headers,
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/media/voice/outbound", json={"to": "+15551110001", "from": "+15550009090"}, headers=headers,
    )
    assert second.status_code == 429
    assert "concurrent" in second.json()["detail"].lower()


def test_concurrent_call_limit_allows_up_to_the_paid_normal_tier(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAconcurrent2", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    from app.risk.service import MAX_CONCURRENT_CALLS_BY_RISK_STATE
    from app.risk.models import AccountRiskState

    token = _signup_and_login(client, "riskconcurrent2@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550009091")
    _promote_to_paid_normal(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    limit = MAX_CONCURRENT_CALLS_BY_RISK_STATE[AccountRiskState.PAID_NORMAL]
    for _ in range(limit):
        response = client.post(
            "/media/voice/outbound", json={"to": "+15551110002", "from": "+15550009091"}, headers=headers,
        )
        assert response.status_code == 200, response.text

    over_limit = client.post(
        "/media/voice/outbound", json={"to": "+15551110002", "from": "+15550009091"}, headers=headers,
    )
    assert over_limit.status_code == 429


def test_completed_call_frees_up_the_concurrent_call_slot(client, db_session, monkeypatch):
    """A call that's actually ended (Twilio status callback delivered) must
    not keep counting against the concurrent-call limit forever - the gate
    is about calls genuinely in flight right now, not a lifetime cap."""
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAconcurrent3", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    token = _signup_and_login(client, "riskconcurrent3@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    _active_number(db_session, account_id, "+15550009092")
    headers = {"Authorization": f"Bearer {token}"}

    first = client.post(
        "/media/voice/outbound", json={"to": "+15551110003", "from": "+15550009092"}, headers=headers,
    )
    assert first.status_code == 200, first.text

    callback_url = "http://testserver/media/voice/status-callback"
    callback_params = {"CallSid": "CAconcurrent3", "CallStatus": "completed", "CallDuration": "10"}
    signature = _twilio_signature(callback_url, callback_params)
    callback_response = client.post(
        "/media/voice/status-callback", data=callback_params, headers={"X-Twilio-Signature": signature}
    )
    assert callback_response.status_code == 204

    second = client.post(
        "/media/voice/outbound", json={"to": "+15551110004", "from": "+15550009092"}, headers=headers,
    )
    assert second.status_code == 200, second.text


def test_kyc_approval_steps_up_trial_low_to_trial_verified(client, db_session):
    from app.compliance.models import ComplianceRule
    from app.numbering.identity.models import Account
    from app.risk.models import AccountRiskState
    from app.staff.models import PlatformStaffRole

    db_session.add(
        ComplianceRule(country="GB", requirement_type="kyc_individual", required_documents=["government_id"])
    )
    db_session.commit()

    token = _signup_and_login(client, "risktrialverified1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    # Clears the billing-trial gate so POST /compliance/cases below isn't
    # blocked, without promoting risk_state away from TRIAL_LOW - this
    # test's whole point is proving KYC approval is what steps it up to
    # TRIAL_VERIFIED, so it must still start at TRIAL_LOW.
    _clear_billing_trial_gate(db_session, account_id)

    case_response = client.post(
        "/compliance/cases", json={"jurisdiction": "GB", "requirement_type": "kyc_individual"}, headers=headers
    )
    case_id = case_response.json()["id"]

    staff_token = _create_staff_and_login(
        client, db_session, "stafftrialverified1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    approve = client.post(
        f"/compliance/cases/{case_id}/approve", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert approve.status_code == 200

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.TRIAL_VERIFIED


def test_number_purchase_steps_up_trial_account_to_paid_normal(client, db_session, monkeypatch):
    from app.numbering.identity.models import Account
    from app.risk.models import AccountRiskState

    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": "PN_fake_riskstepup", "phone_number": e164, "capabilities": {}},
    )
    token = _signup_and_login(client, "riskpaidstepup1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    # Clears the billing-trial gate so the reserve/purchase calls below
    # aren't blocked, without promoting risk_state away from TRIAL_LOW -
    # this test's whole point is proving the purchase itself is what steps
    # it up to PAID_NORMAL, so it must still start at TRIAL_LOW.
    _clear_billing_trial_gate(db_session, account_id)
    client.post("/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers)

    # AU (not one of the shared dev DB's seeded KYC-rule countries -
    # US/GB/CA/NG/ZA/GH/KE/MX - see test_numbers.py's _reserve helper
    # comments) - keeps this test's purchase from being blocked by an
    # unrelated compliance case requirement.
    reserve = client.post(
        "/numbers/reserve", json={"e164": "+15550070090", "country": "AU"}, headers=headers,
    )
    assert reserve.status_code == 201, reserve.text
    purchase = client.post("/numbers/purchase", json={"e164": "+15550070090"}, headers=headers)
    assert purchase.status_code == 200, purchase.text

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.PAID_NORMAL


def test_purchase_does_not_downgrade_an_account_under_review(client, db_session, monkeypatch):
    from app.numbering.identity.models import Account
    from app.risk.models import AccountRiskState

    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": "PN_fake_riskstepup2", "phone_number": e164, "capabilities": {}},
    )
    token = _signup_and_login(client, "riskpaidstepup2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _clear_billing_trial_gate(db_session, account_id)
    client.post("/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers)

    reserve = client.post(
        "/numbers/reserve", json={"e164": "+15550070091", "country": "AU"}, headers=headers,
    )
    assert reserve.status_code == 201, reserve.text

    account = db_session.query(Account).filter(Account.id == account_id).first()
    account.risk_state = AccountRiskState.REVIEW_REQUIRED
    db_session.commit()

    purchase = client.post("/numbers/purchase", json={"e164": "+15550070091"}, headers=headers)
    assert purchase.status_code == 200, purchase.text

    db_session.refresh(account)
    assert account.risk_state == AccountRiskState.REVIEW_REQUIRED


def test_fraud_case_opening_sets_review_required(db_session):
    from app.risk.models import AccountRiskState, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.numbering.identity.models import Account

    account_id = _real_accounts(db_session, 1, "riskstate-review")[0]
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.REVIEW_REQUIRED


def test_auto_suspend_sets_suspended_fraud(db_session):
    from app.risk.models import AccountRiskState, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.numbering.identity.models import Account

    account_id = _real_accounts(db_session, 1, "riskstate-suspend")[0]
    for i in range(3):  # 3 * 40 = 120, capped at 100 -> crosses AUTO_SUSPEND_THRESHOLD
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail=f"t{i}")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.SUSPENDED_FRAUD


def test_clearing_a_fraud_case_restores_the_account_to_its_baseline_tier(client, db_session):
    from app.risk.models import AccountRiskState, FraudCase, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.numbering.identity.models import Account
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "riskstate-clear")[0]
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.REVIEW_REQUIRED
    case = db_session.query(FraudCase).filter(FraudCase.account_id == account_id).first()

    officer_token = _create_staff_and_login(
        client, db_session, "riskstateclearofficer@zoikolocal.com", PlatformStaffRole.COMPLIANCE_OFFICER
    )
    response = client.post(
        f"/risk/fraud-cases/{case.id}/resolve",
        json={"status": "cleared", "notes": "false positive"},
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert response.status_code == 200, response.text

    db_session.refresh(account)
    # No KYC approval, no completed purchase behind this account - baseline
    # falls all the way back to TRIAL_LOW, not straight to PAID_NORMAL.
    assert account.risk_state == AccountRiskState.TRIAL_LOW


def test_confirming_a_fraud_case_forces_suspended_fraud(client, db_session):
    from app.risk.models import AccountRiskState, FraudCase, FraudCaseStatus
    from app.numbering.identity.models import Account
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "riskstate-confirm")[0]
    case = FraudCase(account_id=account_id, score_at_open=70, status=FraudCaseStatus.OPEN)
    db_session.add(case)
    db_session.commit()

    officer_token = _create_staff_and_login(
        client, db_session, "riskstateconfirmofficer@zoikolocal.com", PlatformStaffRole.COMPLIANCE_OFFICER
    )
    response = client.post(
        f"/risk/fraud-cases/{case.id}/resolve",
        json={"status": "confirmed", "notes": "verified abuse"},
        headers={"Authorization": f"Bearer {officer_token}"},
    )
    assert response.status_code == 200, response.text

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.SUSPENDED_FRAUD


def test_staff_reinstatement_restores_baseline_risk_state(client, db_session):
    from app.risk.models import AccountRiskState, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.numbering.identity.models import Account
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus as NumberStatus
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "riskstate-reinstate")[0]
    db_session.add(
        PhoneNumber(e164="+15550070092", country="US", status=NumberStatus.ACTIVE, account_id=account_id)
    )
    db_session.commit()

    for i in range(3):  # crosses AUTO_SUSPEND_THRESHOLD
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail=f"t{i}")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state == AccountRiskState.SUSPENDED_FRAUD

    admin_token = _create_staff_and_login(
        client, db_session, "riskstatereinstateadmin@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    response = client.post(
        f"/risk/accounts/{account_id}/reinstate",
        json={"reason": "confirmed false positive"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text

    db_session.refresh(account)
    # This account has an ACTIVE phone number on file -> baseline is
    # PAID_NORMAL, not TRIAL_LOW.
    assert account.risk_state == AccountRiskState.PAID_NORMAL


def test_account_risk_summary_includes_risk_state(client, db_session):
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "riskstate-summary")[0]
    staff_token = _create_staff_and_login(
        client, db_session, "riskstatesummarystaff@zoikolocal.com", PlatformStaffRole.SUPPORT
    )
    response = client.get(
        f"/risk/accounts/{account_id}/score", headers={"Authorization": f"Bearer {staff_token}"}
    )
    assert response.status_code == 200
    assert response.json()["risk_state"] == "trial_low"


def test_staff_manual_risk_state_override(client, db_session):
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "riskstate-override")[0]
    admin_token = _create_staff_and_login(
        client, db_session, "riskstateoverrideadmin@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    response = client.put(
        f"/risk/accounts/{account_id}/risk-state",
        json={"state": "suspended_fraud", "reason": "external law-enforcement tip"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["risk_state"] == "suspended_fraud"


def test_support_staff_cannot_manually_override_risk_state(client, db_session):
    from app.staff.models import PlatformStaffRole

    account_id = _real_accounts(db_session, 1, "riskstate-override-denied")[0]
    support_token = _create_staff_and_login(
        client, db_session, "riskstateoverridesupport@zoikolocal.com", PlatformStaffRole.SUPPORT
    )
    response = client.put(
        f"/risk/accounts/{account_id}/risk-state",
        json={"state": "paid_normal", "reason": "n/a"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_manual_risk_state_override_rejects_an_unknown_account(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskstateoverrideunknown@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    response = client.put(
        "/risk/accounts/00000000-0000-0000-0000-000000000000/risk-state",
        json={"state": "paid_normal", "reason": "n/a"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404
