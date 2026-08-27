from datetime import datetime, timedelta, timezone

from app.numbering.numbers.models import CallerIdentity, CallerIdentityStatus, PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Fraud Model Test Co",
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


def _place_call(client, headers, to: str, from_number: str):
    return client.post("/media/voice/outbound", json={"to": to, "from": from_number}, headers=headers)


def _clear_billing_trial_gate(db_session, account_id: str) -> None:
    """See test_risk.py's helper of the same name - app.core.deps.
    require_paid_or_read_only blocks write actions (placing a call) for a
    TRIALING account, and also for an ACTIVE one still carrying a
    trial_ends_at (a lapsed trial auto-rolled to ACTIVE with no real
    payment, not a genuine upgrade - change_plan explicitly clears this
    field, so a direct status flip has to as well)."""
    from app.billing.models import SubscriptionStatus
    from app.billing.service import get_or_create_subscription

    sub = get_or_create_subscription(db_session, account_id)
    sub.status = SubscriptionStatus.ACTIVE
    sub.trial_ends_at = None
    db_session.commit()


def _promote_to_paid_normal(db_session, account_id: str) -> None:
    """See test_risk.py's helper of the same name - avoids the new
    AccountRiskState.TRIAL_LOW concurrent-call limit (1) interfering with
    tests placing several outbound calls back-to-back to exercise the
    (unrelated) geographic-dispersion limit."""
    from app.numbering.identity.models import Account
    from app.risk.models import AccountRiskState

    account = db_session.query(Account).filter(Account.id == account_id).first()
    account.risk_state = AccountRiskState.PAID_NORMAL
    db_session.commit()


# --- Geographic dispersion (IRSF pattern) ---

# One real-shaped E.164 number per distinct country, matching phonenumbers'
# own region metadata - these don't need to be live/assigned, just
# structurally valid enough for region_code_for_number to resolve.
_COUNTRY_NUMBERS = [
    "+14155552671",  # US
    "+442071838750",  # GB
    "+33142685300",  # FR
    "+4930123456",  # DE
    "+81312345678",  # JP
]


def test_geographic_dispersion_allowed_below_threshold(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAgeo1", "status": "completed", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    from app.risk.service import GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD

    token = _signup_and_login(client, "fraudgeo1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550070001")
    _clear_billing_trial_gate(db_session, account_id)
    _promote_to_paid_normal(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    for destination in _COUNTRY_NUMBERS[: GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1]:
        response = _place_call(client, headers, destination, "+15550070001")
        assert response.status_code == 200, response.text


def test_geographic_dispersion_blocks_at_threshold(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAgeo2", "status": "completed", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    from app.risk.service import GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD

    token = _signup_and_login(client, "fraudgeo2@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550070002")
    _clear_billing_trial_gate(db_session, account_id)
    _promote_to_paid_normal(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    for destination in _COUNTRY_NUMBERS[: GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1]:
        assert _place_call(client, headers, destination, "+15550070002").status_code == 200

    over_threshold = _place_call(
        client, headers, _COUNTRY_NUMBERS[GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1], "+15550070002"
    )
    assert over_threshold.status_code == 429
    assert "distinct countries" in over_threshold.json()["detail"].lower()


def test_geographic_dispersion_signal_is_recorded(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAgeo3", "status": "completed", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    from app.risk.models import RiskSignal, RiskSignalType
    from app.risk.service import GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD

    token = _signup_and_login(client, "fraudgeo3@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550070003")
    _clear_billing_trial_gate(db_session, account_id)
    _promote_to_paid_normal(db_session, account_id)
    headers = {"Authorization": f"Bearer {token}"}

    for destination in _COUNTRY_NUMBERS[: GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1]:
        _place_call(client, headers, destination, "+15550070003")
    _place_call(client, headers, _COUNTRY_NUMBERS[GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD - 1], "+15550070003")

    signal = (
        db_session.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.signal_type == RiskSignalType.GEOGRAPHIC_DISPERSION)
        .first()
    )
    assert signal is not None


# --- Time-decayed scoring ---

def test_score_decays_with_signal_age(db_session):
    from app.numbering.identity.models import Account, AccountType
    from app.risk.models import RiskSignal, RiskSignalType
    from app.risk.service import compute_account_risk_score, get_signal_weight

    account = Account(name="Decay Test Co", account_type=AccountType.BUSINESS)
    db_session.add(account)
    db_session.flush()

    weight = get_signal_weight(db_session, RiskSignalType.BLOCKED_DESTINATION_ATTEMPT)
    now = datetime.now(timezone.utc)
    db_session.add(
        RiskSignal(
            account_id=account.id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT,
            detail="fresh", created_at=now - timedelta(seconds=1),
        )
    )
    db_session.add(
        RiskSignal(
            account_id=account.id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT,
            detail="old", created_at=now - timedelta(hours=16),
        )
    )
    db_session.commit()

    score = compute_account_risk_score(db_session, account.id)
    # Flat-sum would be 2*weight; decay (16h = two 8h half-lives on the old
    # signal, ~1x on the fresh one) should land meaningfully below that -
    # proving age actually discounts a signal's contribution.
    assert score < 2 * weight
    assert score > weight * 0.5  # the fresh signal alone still counts for most of its weight


# --- FraudRule staff tuning ---

def test_non_admin_staff_cannot_update_fraud_rule(client, db_session):
    from app.staff.models import PlatformStaffRole

    support_token = _create_staff_and_login(client, db_session, "fraudrulesupport@zoikolocal.com", PlatformStaffRole.SUPPORT)
    response = client.put(
        "/risk/fraud-rules/blocked_destination_attempt",
        json={"weight": 5, "is_active": True},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_deactivating_a_signal_stops_it_contributing_to_score(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CArule1", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    from app.risk.models import BlockedDestination
    from app.risk.service import compute_account_risk_score
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(client, db_session, "fraudruleadmin1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN)
    rule_response = client.put(
        "/risk/fraud-rules/blocked_destination_attempt",
        json={"weight": 40, "is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert rule_response.status_code == 200
    assert rule_response.json()["is_active"] is False

    db_session.add(BlockedDestination(prefix="+1901", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudruleaccount1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550070010")
    headers = {"Authorization": f"Bearer {token}"}

    _place_call(client, headers, "+19015551234", "+15550070010")

    assert compute_account_risk_score(db_session, account_id) == 0


# --- FraudCase review queue ---

def test_review_threshold_opens_a_case_without_suspending(client, db_session):
    from app.risk.models import BlockedDestination
    from app.risk.service import record_risk_signal
    from app.risk.models import RiskSignalType

    db_session.add(BlockedDestination(prefix="+1902", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudcaseaccount1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = _active_number(db_session, account_id, "+15550070020")

    # Two BLOCKED_DESTINATION_ATTEMPT signals (default weight 40 each,
    # negligible decay since they just happened) -> ~80, above
    # REVIEW_THRESHOLD (60) but below AUTO_SUSPEND_THRESHOLD (100).
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    db_session.refresh(number)
    assert number.status == PhoneNumberStatus.ACTIVE

    from app.staff.models import PlatformStaffRole
    staff_token = _create_staff_and_login(client, db_session, "fraudcasestaff1@zoikolocal.com", PlatformStaffRole.SUPPORT)
    cases_response = client.get("/risk/fraud-cases", headers={"Authorization": f"Bearer {staff_token}"})
    assert cases_response.status_code == 200
    matching = [c for c in cases_response.json() if c["account_id"] == account_id]
    assert len(matching) == 1
    assert matching[0]["status"] == "open"


def test_auto_suspend_threshold_does_not_also_open_a_case(client, db_session):
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal

    db_session.add(BlockedDestination(prefix="+1903", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudcaseaccount2@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    number = _active_number(db_session, account_id, "+15550070021")

    for i in range(3):  # 3 * 40 = 120, capped at 100 -> crosses AUTO_SUSPEND_THRESHOLD
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail=f"t{i}")

    db_session.refresh(number)
    assert number.status == PhoneNumberStatus.SUSPENDED

    from app.staff.models import PlatformStaffRole
    staff_token = _create_staff_and_login(client, db_session, "fraudcasestaff2@zoikolocal.com", PlatformStaffRole.SUPPORT)
    cases_response = client.get("/risk/fraud-cases", headers={"Authorization": f"Bearer {staff_token}"})
    # The 2nd signal (score 80) legitimately opened a review case before the
    # 3rd pushed the account over AUTO_SUSPEND_THRESHOLD - that case should
    # be auto-resolved (not left dangling "open"), not erased from history.
    matching = [c for c in cases_response.json() if c["account_id"] == account_id]
    assert all(c["status"] != "open" for c in matching)


def test_review_threshold_notifies_the_owner_with_a_warning(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal

    db_session.add(BlockedDestination(prefix="+1906", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudwarning1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]

    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    notifications = client.get("/notifications/me", headers=headers).json()
    matches = [n for n in notifications if n["event_name"] == "trust.account_warning"]
    assert len(matches) == 1
    assert matches[0]["status"] == "sent"


def test_auto_suspend_notifies_the_owner(client, db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal

    db_session.add(BlockedDestination(prefix="+1907", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudsuspend1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _active_number(db_session, account_id, "+15550070022")

    for i in range(3):  # 3 * 40 = 120, capped at 100 -> crosses AUTO_SUSPEND_THRESHOLD
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail=f"t{i}")

    notifications = client.get("/notifications/me", headers=headers).json()
    matches = [n for n in notifications if n["event_name"] == "trust.account_suspended_or_disabled"]
    assert len(matches) == 1
    assert matches[0]["status"] == "sent"


def test_resolve_fraud_case(client, db_session):
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.staff.models import PlatformStaffRole

    db_session.add(BlockedDestination(prefix="+1904", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudcaseaccount3@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550070022")

    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    staff_token = _create_staff_and_login(
        client, db_session, "fraudcasestaff3@zoikolocal.com", PlatformStaffRole.COMPLIANCE_OFFICER
    )
    cases = client.get("/risk/fraud-cases", headers={"Authorization": f"Bearer {staff_token}"}).json()
    case_id = next(c["id"] for c in cases if c["account_id"] == account_id)

    resolve_response = client.post(
        f"/risk/fraud-cases/{case_id}/resolve",
        json={"status": "cleared", "notes": "confirmed legitimate business traffic"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["status"] == "cleared"
    assert resolve_response.json()["resolved_by"] is not None

    second_attempt = client.post(
        f"/risk/fraud-cases/{case_id}/resolve",
        json={"status": "confirmed"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert second_attempt.status_code == 409


def test_resolving_as_open_is_rejected(client, db_session):
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.staff.models import PlatformStaffRole

    db_session.add(BlockedDestination(prefix="+1905", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "fraudcaseaccount4@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550070023")

    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    staff_token = _create_staff_and_login(client, db_session, "fraudcasestaff4@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN)
    cases = client.get("/risk/fraud-cases", headers={"Authorization": f"Bearer {staff_token}"}).json()
    case_id = next(c["id"] for c in cases if c["account_id"] == account_id)

    response = client.post(
        f"/risk/fraud-cases/{case_id}/resolve",
        json={"status": "open"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 409


# --- Trial/fraud control plane (Production Readiness Standard §5.3/Table 15-16) ---


def test_new_account_starts_trial_low(client, db_session):
    from app.numbering.identity.models import Account

    token = _signup_and_login(client, "riskstatenew1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state.value == "trial_low"


def test_review_threshold_sets_risk_state_to_review_required(client, db_session):
    from app.numbering.identity.models import Account
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal

    db_session.add(BlockedDestination(prefix="+1910", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "riskstatereview1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state.value == "review_required"


def test_auto_suspend_sets_risk_state_to_suspended_fraud(client, db_session):
    from app.numbering.identity.models import Account
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal

    db_session.add(BlockedDestination(prefix="+1911", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "riskstatesuspend1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    for i in range(3):
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail=f"t{i}")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state.value == "suspended_fraud"


def test_resolve_cleared_restores_baseline_risk_state(client, db_session):
    from app.numbering.identity.models import Account
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.staff.models import PlatformStaffRole

    db_session.add(BlockedDestination(prefix="+1912", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "riskstateclear1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t1")
    record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail="t2")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state.value == "review_required"

    staff_token = _create_staff_and_login(client, db_session, "riskstateclearstaff1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN)
    cases = client.get("/risk/fraud-cases", headers={"Authorization": f"Bearer {staff_token}"}).json()
    case_id = next(c["id"] for c in cases if c["account_id"] == account_id)
    client.post(
        f"/risk/fraud-cases/{case_id}/resolve", json={"status": "cleared"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )

    db_session.refresh(account)
    assert account.risk_state.value == "trial_low"


def test_reinstate_restores_risk_state_from_suspended_fraud(client, db_session):
    from app.numbering.identity.models import Account
    from app.risk.models import BlockedDestination, RiskSignalType
    from app.risk.service import record_risk_signal
    from app.staff.models import PlatformStaffRole

    db_session.add(BlockedDestination(prefix="+1913", reason="test prefix"))
    db_session.commit()

    token = _signup_and_login(client, "riskstatereinstate1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    for i in range(3):
        record_risk_signal(db_session, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT, detail=f"t{i}")

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state.value == "suspended_fraud"

    staff_token = _create_staff_and_login(
        client, db_session, "riskstatereinstatestaff1@zoikolocal.com", PlatformStaffRole.COMPLIANCE_OFFICER
    )
    reinstate = client.post(
        f"/risk/accounts/{account_id}/reinstate", json={"reason": "false positive"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert reinstate.status_code == 200

    db_session.refresh(account)
    assert account.risk_state.value == "trial_low"


def test_paid_plan_sets_baseline_risk_state_to_paid_normal(client, db_session):
    from app.numbering.identity.models import Account

    token = _signup_and_login(client, "riskstatepaid1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]

    response = client.put("/billing/subscription/plan", json={"plan_code": "starter"}, headers=headers)
    assert response.status_code == 200

    account = db_session.query(Account).filter(Account.id == account_id).first()
    assert account.risk_state.value == "paid_normal"


def test_concurrency_limit_blocks_extra_simultaneous_calls(client, db_session, monkeypatch):
    call_count = {"n": 0}

    def _fake_place_call(**kwargs):
        call_count["n"] += 1
        return {"sid": f"CA_fake_{call_count['n']}", "status": "in-progress", "to": kwargs["to"], "from": kwargs["from_"]}

    monkeypatch.setattr("app.media.service.telecom.place_call", _fake_place_call)

    from app.risk.models import AccountRiskState
    from app.risk.service import MAX_CONCURRENT_CALLS_BY_RISK_STATE

    token = _signup_and_login(client, "concurrencylimit1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _active_number(db_session, account_id, "+15550070030")
    _clear_billing_trial_gate(db_session, account_id)

    # A fresh signup defaults to TRIAL_LOW - read the real configured limit
    # rather than hardcoding it a second time here (this exact test used to
    # assume 3, went stale when the tier was tuned down to 1, and the
    # resulting 429-vs-200 mismatch was masked by an unrelated infra bug
    # for a while - see the migration that fixed the real root cause). Same
    # tier/limit test_risk.py's
    # test_concurrent_call_limit_blocks_a_second_in_flight_call_for_a_trial_account
    # covers - kept here too since it's this suite's own fraud-model
    # regression check for the same code path.
    limit = MAX_CONCURRENT_CALLS_BY_RISK_STATE[AccountRiskState.TRIAL_LOW]
    for i in range(limit):
        response = _place_call(client, headers, f"+1416555000{i}", "+15550070030")
        assert response.status_code == 200, response.text

    over_limit = _place_call(client, headers, "+14165550009", "+15550070030")
    assert over_limit.status_code == 429


def test_cumulative_trial_usage_blocks_after_lifetime_cap(client, db_session):
    from app.usage.service import record_usage_event

    token = _signup_and_login(client, "trialcap1@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    # MAX_TRIAL_LIFETIME_SPEND_CENTS is 2000 ($20) - record enough rated
    # call_seconds usage to exceed it, then confirm the guard trips.
    record_usage_event(
        db_session, account_id=account_id, event_type="call_seconds", quantity=100000, unit="seconds",
        country_band=None, idempotency_key="trialcap-usage-1",
    )
    from app.usage.models import UsageEvent

    event = db_session.query(UsageEvent).filter(UsageEvent.idempotency_key == "trialcap-usage-1").first()
    event.estimated_cost_cents = 2500
    db_session.commit()

    from app.risk.service import CumulativeTrialUsageExceededError, assert_cumulative_trial_usage_ok

    try:
        assert_cumulative_trial_usage_ok(db_session, account_id)
        assert False, "expected CumulativeTrialUsageExceededError"
    except CumulativeTrialUsageExceededError:
        pass


def test_account_kill_switch_blocks_outbound_calling_for_one_account(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CA_fake", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )

    token = _signup_and_login(client, "accountkillswitch1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    _active_number(db_session, account_id, "+15550070040")
    _clear_billing_trial_gate(db_session, account_id)

    from app.staff.models import PlatformStaffRole
    staff_token = _create_staff_and_login(client, db_session, "accountkillswitchstaff1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN)

    activate = client.post(
        f"/risk/accounts/{account_id}/kill-switches/outbound_calling/activate",
        json={"reason": "test lockdown"}, headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert activate.status_code == 200
    assert activate.json()["is_active"] is True

    blocked = _place_call(client, headers, "+14165550099", "+15550070040")
    assert blocked.status_code == 503

    deactivate = client.post(
        f"/risk/accounts/{account_id}/kill-switches/outbound_calling/deactivate",
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert deactivate.status_code == 200
    assert deactivate.json()["is_active"] is False

    allowed = _place_call(client, headers, "+14165550098", "+15550070040")
    assert allowed.status_code == 200
