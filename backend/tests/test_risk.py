from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


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


def _create_staff_and_login(client, db_session, email: str, role):
    from app.staff import service as staff_service

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _active_number(db_session, account_id: str, e164: str) -> PhoneNumber:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    return number


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
    _active_number(db_session, account_id, "+15550008888")
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
    _active_number(db_session, account_id, "+15550007777")
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
