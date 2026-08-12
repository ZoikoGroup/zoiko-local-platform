import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from app.billing import service
from app.billing.models import (
    Subscription,
    SubscriptionStatus,
    ZoikoNexReconciliationExceptionType,
    ZoikoNexSyncEvent,
    ZoikoNexSyncEventType,
)
from app.numbering.identity.models import Account, AccountType
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _make_account(db_session, name: str) -> Account:
    account = Account(name=name, account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    return account


def _create_and_login_staff(db_session, client, email: str, role=PlatformStaffRole.SUPPORT) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "ZoikoNex Test Co", "account_type": "individual", "email": email, "password": "supersecret123"},
    )
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def _stub_sync_subscription(monkeypatch, *, account_id_suffix: str = "1"):
    """Real ZoikoNex calls (app.integrations.billing.zoikonex.sync_subscription)
    make genuine HTTP requests against a running ZoikoNex stack - same
    "mock the provider, don't hit the network" discipline as Twilio/Stripe
    elsewhere in this test suite. Returns the fake ids used, so a test can
    assert against them directly instead of a hardcoded prefix."""
    ids = {"party_id": f"zn-party-{account_id_suffix}", "customer_id": f"zn-cust-{account_id_suffix}", "account_id": f"zn-acct-{account_id_suffix}"}

    def _fake_sync_subscription(db, sub, *, account_type):
        sub.zoikonex_party_id = ids["party_id"]
        sub.zoikonex_customer_id = ids["customer_id"]
        sub.zoikonex_account_id = ids["account_id"]
        return dict(ids)

    monkeypatch.setattr("app.billing.service.zoikonex_adapter.sync_subscription", _fake_sync_subscription)
    return ids


def _stub_sync_usage_event(monkeypatch, *, ref: str = "zn-usage-ref-1"):
    monkeypatch.setattr(
        "app.billing.service.zoikonex_adapter.sync_usage_event",
        lambda db, sub, usage_event_id, **kwargs: {"zoikonex_ref": ref, "status": "NORMALISED"},
    )


# --- Sync mechanics ---


def test_new_subscription_is_synced_to_zoikonex_on_creation(db_session, monkeypatch):
    ids = _stub_sync_subscription(monkeypatch)
    account = _make_account(db_session, "Sync New Sub Co")
    sub = service.get_or_create_subscription(db_session, account.id)

    assert sub.zoikonex_ref == ids["account_id"]

    events = db_session.query(ZoikoNexSyncEvent).filter(ZoikoNexSyncEvent.account_id == account.id).all()
    assert len(events) == 1
    assert events[0].event_type == ZoikoNexSyncEventType.SUBSCRIPTION_SYNC


def test_change_plan_re_syncs_to_zoikonex(db_session, monkeypatch):
    _stub_sync_subscription(monkeypatch)
    account = _make_account(db_session, "Sync Change Plan Co")
    service.get_or_create_subscription(db_session, account.id)

    service.change_plan(db_session, account.id, "starter", actor="test-actor")

    events = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.SUBSCRIPTION_SYNC)
        .all()
    )
    assert len(events) == 2  # once at creation, once on plan change
    assert events[-1].payload["plan_code"] == "starter"


def test_recording_usage_syncs_it_to_zoikonex(client, db_session, monkeypatch):
    from app.usage.service import record_usage_event

    _stub_sync_subscription(monkeypatch)
    _stub_sync_usage_event(monkeypatch, ref="zn-usage-ref-42")
    token = _signup_and_login(client, "zoikonexusage1@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()

    record_usage_event(
        db_session, account_id=me["account_id"], event_type="call_seconds", quantity=42, unit="seconds",
        country_band="US", idempotency_key="zn-usage-test-1",
    )

    events = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == me["account_id"], ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.USAGE_SYNC)
        .all()
    )
    assert len(events) == 1
    assert events[0].payload["quantity"] == 42
    assert events[0].zoikonex_ref == "zn-usage-ref-42"


def test_duplicate_usage_event_is_not_synced_twice(db_session, monkeypatch):
    from app.usage.service import record_usage_event

    _stub_sync_subscription(monkeypatch)
    _stub_sync_usage_event(monkeypatch)
    account = _make_account(db_session, "Sync Dup Usage Co")
    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=10, unit="seconds",
        country_band=None, idempotency_key="zn-usage-dup-1",
    )
    record_usage_event(  # same idempotency_key - a no-op
        db_session, account_id=account.id, event_type="call_seconds", quantity=10, unit="seconds",
        country_band=None, idempotency_key="zn-usage-dup-1",
    )

    events = db_session.query(ZoikoNexSyncEvent).filter(ZoikoNexSyncEvent.account_id == account.id).all()
    assert len(events) == 1


# --- Payment simulation + grace period ---


def test_simulate_payment_failed_sets_past_due_with_grace_period(db_session):
    account = _make_account(db_session, "Payment Failed Co")
    service.get_or_create_subscription(db_session, account.id)

    sub = service.simulate_zoikonex_payment_event(db_session, account.id, "payment_failed", actor="test-actor")
    assert sub.status == SubscriptionStatus.PAST_DUE
    assert sub.grace_period_ends_at is not None

    events = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.PAYMENT_EVENT_RECEIVED)
        .all()
    )
    assert len(events) == 1


def test_simulate_payment_restored_clears_past_due(db_session):
    account = _make_account(db_session, "Payment Restored Co")
    service.get_or_create_subscription(db_session, account.id)
    service.simulate_zoikonex_payment_event(db_session, account.id, "payment_failed", actor="test-actor")

    sub = service.simulate_zoikonex_payment_event(db_session, account.id, "payment_restored", actor="test-actor")
    assert sub.status == SubscriptionStatus.ACTIVE
    assert sub.grace_period_ends_at is None


def test_simulate_payment_retry_does_not_change_status(db_session):
    account = _make_account(db_session, "Payment Retry Co")
    service.get_or_create_subscription(db_session, account.id)
    service.simulate_zoikonex_payment_event(db_session, account.id, "payment_failed", actor="test-actor")

    sub = service.simulate_zoikonex_payment_event(db_session, account.id, "payment_retry", actor="test-actor")
    assert sub.status == SubscriptionStatus.PAST_DUE  # unchanged


def test_simulate_payment_event_rejects_unknown_type(db_session):
    account = _make_account(db_session, "Payment Bad Type Co")
    service.get_or_create_subscription(db_session, account.id)

    try:
        service.simulate_zoikonex_payment_event(db_session, account.id, "not_a_real_event", actor="test-actor")
        assert False, "expected InvalidPaymentEventError"
    except service.InvalidPaymentEventError:
        pass


def test_assert_billing_not_suspended_allows_active_account(db_session):
    account = _make_account(db_session, "Active Billing Co")
    service.get_or_create_subscription(db_session, account.id)
    service.assert_billing_not_suspended(db_session, account.id)  # must not raise


def test_assert_billing_not_suspended_allows_past_due_within_grace(db_session):
    account = _make_account(db_session, "Grace Period Co")
    service.get_or_create_subscription(db_session, account.id)
    service.simulate_zoikonex_payment_event(db_session, account.id, "payment_failed", actor="test-actor")
    service.assert_billing_not_suspended(db_session, account.id)  # still within the 7-day grace window


def test_assert_billing_not_suspended_blocks_after_grace_period_expires(db_session):
    account = _make_account(db_session, "Expired Grace Co")
    sub = service.get_or_create_subscription(db_session, account.id)
    sub.status = SubscriptionStatus.PAST_DUE
    sub.grace_period_ends_at = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    try:
        service.assert_billing_not_suspended(db_session, account.id)
        assert False, "expected BillingSuspendedError"
    except service.BillingSuspendedError:
        pass


# --- Graceful degradation wired into real features ---


def _suspend(db_session, account_id: str):
    service.get_or_create_subscription(db_session, account_id)
    service.simulate_zoikonex_payment_event(db_session, account_id, "payment_failed", actor="test-actor")
    sub = db_session.query(Subscription).filter(Subscription.account_id == account_id).first()
    sub.grace_period_ends_at = datetime.now(timezone.utc) - timedelta(days=1)  # already expired
    db_session.commit()


def test_outbound_call_is_blocked_when_billing_suspended(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164: {"sid": "PN_fake_zoikonex_outbound", "phone_number": e164, "capabilities": {}},
    )
    token = _signup_and_login(client, "zoikonexoutbound1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers)
    me = client.get("/auth/me", headers=headers).json()

    from_number = "+15550005678"
    client.post("/numbers/reserve", json={"e164": from_number, "country": "US"}, headers=headers)
    purchase = client.post("/numbers/purchase", json={"e164": from_number}, headers=headers)
    assert purchase.status_code == 200, purchase.text

    _suspend(db_session, me["account_id"])

    response = client.post(
        "/media/voice/outbound",
        json={"to": "+15550001234", "from_number": from_number, "message": "hi"},
        headers=headers,
    )
    assert response.status_code == 402


def test_video_room_creation_is_blocked_when_billing_suspended(client, db_session):
    token = _signup_and_login(client, "zoikonexvideo1@example.com")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    _suspend(db_session, me["account_id"])

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 402


def test_number_purchase_is_blocked_when_billing_suspended(client, db_session):
    token = _signup_and_login(client, "zoikonexnumber1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers)
    me = client.get("/auth/me", headers=headers).json()

    client.post("/numbers/reserve", json={"e164": "+15550009999", "country": "US"}, headers=headers)
    _suspend(db_session, me["account_id"])

    response = client.post("/numbers/purchase", json={"e164": "+15550009999"}, headers=headers)
    assert response.status_code == 402


# --- Real inbound payment webhook ---


def _sign_zoikonex_webhook(secret: str, body: bytes) -> str:
    return f"sha256={hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}"


def test_zoikonex_webhook_rejects_missing_secret_configured(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.billing.zoikonex.settings.zoikonex_webhook_secret", "")
    body = json.dumps({"zoikonex_ref": "zn_sub_x", "event_type": "payment_failed"}).encode()
    response = client.post(
        "/billing/zoikonex/webhook", content=body, headers={"X-ZoikoNex-Signature": "sha256=whatever"}
    )
    assert response.status_code == 403


def test_zoikonex_webhook_rejects_invalid_signature(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.billing.zoikonex.settings.zoikonex_webhook_secret", "whsec_zn_test")
    body = json.dumps({"zoikonex_ref": "zn_sub_x", "event_type": "payment_failed"}).encode()
    response = client.post(
        "/billing/zoikonex/webhook", content=body, headers={"X-ZoikoNex-Signature": "sha256=not-the-real-signature"}
    )
    assert response.status_code == 403


def test_zoikonex_webhook_rejects_missing_signature_header(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.billing.zoikonex.settings.zoikonex_webhook_secret", "whsec_zn_test")
    body = json.dumps({"zoikonex_ref": "zn_sub_x", "event_type": "payment_failed"}).encode()
    response = client.post("/billing/zoikonex/webhook", content=body)
    assert response.status_code == 403


def test_zoikonex_webhook_returns_404_for_unknown_zoikonex_ref(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.billing.zoikonex.settings.zoikonex_webhook_secret", "whsec_zn_test")
    body = json.dumps({"zoikonex_ref": "zn_sub_does_not_exist", "event_type": "payment_failed"}).encode()
    response = client.post(
        "/billing/zoikonex/webhook",
        content=body,
        headers={"X-ZoikoNex-Signature": _sign_zoikonex_webhook("whsec_zn_test", body)},
    )
    assert response.status_code == 404


def test_zoikonex_webhook_applies_payment_failed_event(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.billing.zoikonex.settings.zoikonex_webhook_secret", "whsec_zn_test")
    account = _make_account(db_session, "Real Webhook Payment Co")
    sub = service.get_or_create_subscription(db_session, account.id)

    body = json.dumps(
        {"event_id": "evt_1", "zoikonex_ref": sub.zoikonex_ref, "event_type": "payment_failed"}
    ).encode()
    response = client.post(
        "/billing/zoikonex/webhook",
        content=body,
        headers={"X-ZoikoNex-Signature": _sign_zoikonex_webhook("whsec_zn_test", body)},
    )
    assert response.status_code == 204

    db_session.refresh(sub)
    assert sub.status == SubscriptionStatus.PAST_DUE
    assert sub.grace_period_ends_at is not None


def test_zoikonex_webhook_is_idempotent_on_duplicate_event_id(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.billing.zoikonex.settings.zoikonex_webhook_secret", "whsec_zn_test")
    account = _make_account(db_session, "Real Webhook Idempotent Co")
    sub = service.get_or_create_subscription(db_session, account.id)

    body = json.dumps(
        {"event_id": "evt_dup_1", "zoikonex_ref": sub.zoikonex_ref, "event_type": "payment_failed"}
    ).encode()
    headers = {"X-ZoikoNex-Signature": _sign_zoikonex_webhook("whsec_zn_test", body)}

    first = client.post("/billing/zoikonex/webhook", content=body, headers=headers)
    assert first.status_code == 204
    second = client.post("/billing/zoikonex/webhook", content=body, headers=headers)
    assert second.status_code == 204

    events = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.PAYMENT_EVENT_RECEIVED)
        .all()
    )
    assert len(events) == 1  # the duplicate delivery was skipped, not double-applied


# --- Staff routes ---


def test_simulate_payment_event_route_requires_super_admin(client, db_session):
    account = _make_account(db_session, "Staff Route Perms Co")
    service.get_or_create_subscription(db_session, account.id)

    support_token = _create_and_login_staff(db_session, client, "zoikonexstaff1@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    response = client.post(
        "/billing/zoikonex/simulate-payment-event",
        json={"account_id": account.id, "event_type": "payment_failed"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_simulate_payment_event_route_succeeds_for_super_admin(client, db_session):
    account = _make_account(db_session, "Staff Route Success Co")
    service.get_or_create_subscription(db_session, account.id)

    admin_token = _create_and_login_staff(db_session, client, "zoikonexstaff2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    response = client.post(
        "/billing/zoikonex/simulate-payment-event",
        json={"account_id": account.id, "event_type": "payment_failed"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "past_due"


def test_sync_log_route_is_readable_by_any_staff_role(client, db_session):
    account = _make_account(db_session, "Sync Log Route Co")
    service.get_or_create_subscription(db_session, account.id)

    token = _create_and_login_staff(db_session, client, "zoikonexstaff3@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    response = client.get(
        "/billing/zoikonex/sync-log", params={"account_id": account.id}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_reconciliation_route_returns_matching_counts(client, db_session):
    from app.usage.service import record_usage_event

    account = _make_account(db_session, "Reconciliation Co")
    service.get_or_create_subscription(db_session, account.id)
    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=5, unit="seconds",
        country_band=None, idempotency_key="zn-reconciliation-test-1",
    )

    token = _create_and_login_staff(db_session, client, "zoikonexstaff4@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    response = client.get("/billing/zoikonex/reconciliation", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["synced_subscriptions"] <= body["total_subscriptions"]
    assert body["synced_usage_events"] <= body["total_usage_events"]
    assert body["unsynced_subscriptions"] == body["total_subscriptions"] - body["synced_subscriptions"]


# --- Reconciliation job (Architecture doc §9 "daily reconciliation jobs... exceptions must enter an operations queue") ---


def test_reconciliation_run_finds_no_exceptions_when_everything_synced(db_session):
    from app.usage.service import record_usage_event

    account = _make_account(db_session, "Clean Reconciliation Co")
    service.get_or_create_subscription(db_session, account.id)
    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=5, unit="seconds",
        country_band=None, idempotency_key="zn-recon-run-clean-1",
    )

    run = service.run_zoikonex_reconciliation(db_session)

    # Subscriptions/usage events created by THIS test are fully synced -
    # unlike exceptions_found below, these two counters aren't polluted by
    # the shared dev DB's pre-existing call history, since this dev DB has
    # no pre-existing subscription/usage-sync drift (only real leftover
    # calls from manual end-to-end testing, which the carrier-evidence leg
    # now legitimately flags - see the baseline-absorb pattern in the
    # tests below for why exceptions_found isn't asserted == 0 here).
    assert run.unsynced_subscriptions == 0
    assert run.unsynced_usage_events == 0


def test_reconciliation_run_detects_usage_event_missing_sync(db_session):
    from app.usage.models import UsageEvent

    # Absorbs whatever pre-existing drift already sits in this shared dev
    # DB (e.g. real completed calls from manual end-to-end testing that
    # the carrier-evidence leg below also watches) as "already open"
    # before this test's own scenario, so exceptions_found below reflects
    # only what THIS test adds - the environment isn't a pristine empty
    # database, and other reconciliation tests in this file legitimately
    # add their own drift too.
    service.run_zoikonex_reconciliation(db_session)

    account = _make_account(db_session, "Drifted Usage Co")
    service.get_or_create_subscription(db_session, account.id)
    # Bypasses record_usage_event (and therefore sync_usage_event_to_zoikonex)
    # entirely, simulating the exact drift a sync failure after the usage
    # event's own commit would leave behind.
    event = UsageEvent(
        account_id=account.id, event_type="call_seconds", quantity=10, unit="seconds",
        country_band=None, idempotency_key="zn-recon-drift-usage-1",
    )
    db_session.add(event)
    db_session.commit()
    db_session.refresh(event)

    run = service.run_zoikonex_reconciliation(db_session)

    assert run.unsynced_usage_events == 1
    assert run.exceptions_found == 1

    exceptions = service.list_zoikonex_reconciliation_exceptions(db_session, resolved=False)
    matching = [e for e in exceptions if e.subject_id == event.id]
    assert len(matching) == 1
    assert matching[0].exception_type == ZoikoNexReconciliationExceptionType.USAGE_EVENT_MISSING_SYNC
    assert matching[0].account_id == account.id


def test_reconciliation_run_detects_completed_call_missing_usage_event(db_session):
    """The carrier-evidence leg (Commercial Billing Operating Standard
    doc's "three-way reconciliation") - a CallRecord Twilio's own status-
    callback marked completed, with no matching call_seconds UsageEvent,
    is exactly the drift a failed/skipped app.media.service.
    update_call_status usage-metering call would leave behind.

    Baseline counts are captured first, not assumed to start at zero -
    this shared dev DB carries real completed calls from manual end-to-end
    testing elsewhere in the project, which this same carrier-evidence leg
    legitimately also flags."""
    from app.media.models import CallDirection, CallRecord

    baseline = service.run_zoikonex_reconciliation(db_session)

    account = _make_account(db_session, "Carrier Leg Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAcarrierleg1", status="completed", duration=42,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)

    run = service.run_zoikonex_reconciliation(db_session)

    assert run.total_completed_calls == baseline.total_completed_calls + 1
    assert run.unmatched_completed_calls == baseline.unmatched_completed_calls + 1
    assert run.exceptions_found == 1  # baseline run above already absorbed any pre-existing drift as "open"

    exceptions = service.list_zoikonex_reconciliation_exceptions(db_session, resolved=False)
    matching = [e for e in exceptions if e.subject_id == call.id]
    assert len(matching) == 1
    assert matching[0].exception_type == ZoikoNexReconciliationExceptionType.CALL_RECORD_MISSING_USAGE_EVENT
    assert matching[0].account_id == account.id


def test_reconciliation_run_does_not_flag_a_completed_call_with_matching_usage_event(db_session):
    from app.media.models import CallDirection, CallRecord
    from app.usage.service import record_usage_event

    baseline = service.run_zoikonex_reconciliation(db_session)

    account = _make_account(db_session, "Carrier Leg Matched Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAcarrierleg2", status="completed", duration=30,
    )
    db_session.add(call)
    db_session.commit()

    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=30, unit="seconds",
        country_band=None, idempotency_key="call_seconds:CAcarrierleg2",
    )

    run = service.run_zoikonex_reconciliation(db_session)

    assert run.total_completed_calls == baseline.total_completed_calls + 1
    assert run.unmatched_completed_calls == baseline.unmatched_completed_calls


def test_reconciliation_run_flags_a_usage_event_recorded_long_after_the_call(db_session):
    """P0-8 late-event detection - a call_seconds UsageEvent is matched (not
    CALL_RECORD_MISSING_USAGE_EVENT), but its own created_at lands more than
    LATE_EVENT_THRESHOLD after the call it bills for, which is exactly the
    drift a delayed/retried status-callback would leave behind.

    CallRecord.created_at is backdated manually after insert - Postgres
    freezes now() per-transaction, so the call and its usage event would
    otherwise get an identical timestamp within this one test transaction
    (same fix as test_billing_cycle.py's most-recently-created ordering
    test)."""
    from app.media.models import CallDirection, CallRecord
    from app.usage.service import record_usage_event

    baseline = service.run_zoikonex_reconciliation(db_session)

    account = _make_account(db_session, "Late Usage Event Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAlateusage1", status="completed", duration=30,
    )
    db_session.add(call)
    db_session.commit()
    db_session.refresh(call)
    call.created_at = datetime.now(timezone.utc) - timedelta(hours=30)
    db_session.commit()

    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=30, unit="seconds",
        country_band=None, idempotency_key="call_seconds:CAlateusage1",
    )

    run = service.run_zoikonex_reconciliation(db_session)

    assert run.unmatched_completed_calls == baseline.unmatched_completed_calls  # matched, not missing
    assert run.late_usage_events == baseline.late_usage_events + 1

    exceptions = service.list_zoikonex_reconciliation_exceptions(db_session, resolved=False)
    matching = [e for e in exceptions if e.exception_type == ZoikoNexReconciliationExceptionType.LATE_USAGE_EVENT]
    assert len(matching) == 1
    assert matching[0].account_id == account.id


def test_reconciliation_run_does_not_flag_a_usage_event_recorded_promptly(db_session):
    from app.media.models import CallDirection, CallRecord
    from app.usage.service import record_usage_event

    baseline = service.run_zoikonex_reconciliation(db_session)

    account = _make_account(db_session, "Prompt Usage Event Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CApromptusage1", status="completed", duration=30,
    )
    db_session.add(call)
    db_session.commit()

    record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=30, unit="seconds",
        country_band=None, idempotency_key="call_seconds:CApromptusage1",
    )

    run = service.run_zoikonex_reconciliation(db_session)

    assert run.late_usage_events == baseline.late_usage_events


# --- Wholesale call cost capture (P0-8 "retail vs wholesale reconciliation") ---


def test_capture_wholesale_call_cost_stores_twilios_real_price(db_session, monkeypatch):
    """Scoping the mock by call_sid (rather than returning the same price
    for every call) matters here: this shared dev DB carries other
    completed calls with no wholesale cost yet from earlier tests/manual
    testing in this same reconciliation suite (see the carrier-leg tests'
    own baseline-pattern comment above), and they'd otherwise get swept
    into this run and stamped with a price that was never really theirs.
    limit=1000 makes sure this test's own (newest, so last-in-order) call
    is still within the batch even if that backlog is non-trivial."""
    from app.media.models import CallDirection, CallRecord

    account = _make_account(db_session, "Wholesale Capture Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAwholesale1", status="completed", duration=60,
    )
    db_session.add(call)
    db_session.commit()

    def _get_call(call_sid):
        if call_sid == "CAwholesale1":
            return {"sid": call_sid, "price": "-0.03500", "price_unit": "usd"}
        return {"sid": call_sid, "price": None, "price_unit": None}

    monkeypatch.setattr("app.billing.service.telecom.get_call", _get_call)

    result = service.capture_wholesale_call_cost(db_session, limit=1000)

    assert result["captured"] >= 1
    db_session.refresh(call)
    assert call.wholesale_cost_cents == 4  # round(0.035 * 100) = 3.5 -> 4
    assert call.wholesale_currency == "USD"


def test_capture_wholesale_call_cost_leaves_a_not_yet_rated_call_alone(db_session, monkeypatch):
    from app.media.models import CallDirection, CallRecord

    account = _make_account(db_session, "Wholesale Not Rated Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAwholesale2", status="completed", duration=60,
    )
    db_session.add(call)
    db_session.commit()

    monkeypatch.setattr(
        "app.billing.service.telecom.get_call",
        lambda call_sid: {"sid": call_sid, "price": None, "price_unit": None},
    )

    result = service.capture_wholesale_call_cost(db_session, limit=1000)

    assert result["captured"] == 0  # this mock never returns a price, for this call or any other
    db_session.refresh(call)
    assert call.wholesale_cost_cents is None


def test_capture_wholesale_call_cost_counts_a_telecom_error_and_moves_on(db_session, monkeypatch):
    from app.media.models import CallDirection, CallRecord
    from app.integrations.telecom.twilio import TelecomError

    account = _make_account(db_session, "Wholesale Error Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAwholesale3", status="completed", duration=60,
    )
    db_session.add(call)
    db_session.commit()

    def _raise(call_sid):
        raise TelecomError("boom")

    monkeypatch.setattr("app.billing.service.telecom.get_call", _raise)

    result = service.capture_wholesale_call_cost(db_session, limit=1000)

    assert result["errors"] >= 1
    assert result["captured"] == 0
    db_session.refresh(call)
    assert call.wholesale_cost_cents is None


def test_wholesale_reconciliation_summary_compares_retail_and_wholesale(db_session):
    from app.media.models import CallDirection, CallRecord
    from app.usage.service import record_usage_event

    baseline = service.get_wholesale_reconciliation_summary(db_session)

    account = _make_account(db_session, "Wholesale Summary Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAwholesale4", status="completed", duration=60,
        wholesale_cost_cents=4, wholesale_currency="USD",
    )
    db_session.add(call)
    db_session.commit()

    event = record_usage_event(
        db_session, account_id=account.id, event_type="call_seconds", quantity=60, unit="seconds",
        country_band=None, idempotency_key="call_seconds:CAwholesale4",
    )
    event.estimated_cost_cents = 10
    db_session.commit()

    summary = service.get_wholesale_reconciliation_summary(db_session)

    assert summary["calls_with_wholesale_cost"] == baseline["calls_with_wholesale_cost"] + 1
    assert summary["retail_cost_cents"] == baseline["retail_cost_cents"] + 10
    assert summary["wholesale_cost_cents"] == baseline["wholesale_cost_cents"] + 4


def test_wholesale_reconciliation_summary_counts_missing_wholesale_cost(db_session):
    from app.media.models import CallDirection, CallRecord

    baseline = service.get_wholesale_reconciliation_summary(db_session)

    account = _make_account(db_session, "Wholesale Missing Co")
    call = CallRecord(
        account_id=account.id, direction=CallDirection.OUTBOUND,
        from_number="+15550001111", to_number="+15550002222",
        provider_call_sid="CAwholesale5", status="completed", duration=60,
    )
    db_session.add(call)
    db_session.commit()

    summary = service.get_wholesale_reconciliation_summary(db_session)

    assert summary["calls_missing_wholesale_cost"] == baseline["calls_missing_wholesale_cost"] + 1


def test_wholesale_cost_capture_route_requires_staff_auth(client):
    response = client.post("/billing/zoikonex/reconciliation/wholesale-cost-capture/run")
    assert response.status_code == 401


def test_wholesale_summary_route_returns_data_for_any_staff_role(client, db_session):
    token = _create_and_login_staff(
        db_session, client, "wholesalesummarystaff@zoikolocal.com", role=PlatformStaffRole.SUPPORT
    )
    response = client.get("/billing/zoikonex/reconciliation/wholesale-summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert "calls_with_wholesale_cost" in body


def test_reconciliation_run_detects_subscription_missing_zoikonex_ref(db_session):
    service.run_zoikonex_reconciliation(db_session)  # absorb pre-existing drift, see comment above

    account = _make_account(db_session, "Drifted Subscription Co")
    now = datetime.now(timezone.utc)
    # Bypasses get_or_create_subscription (and therefore
    # sync_subscription_to_zoikonex) entirely - zoikonex_ref stays NULL.
    sub = Subscription(
        account_id=account.id, plan_code="free_trial", status=SubscriptionStatus.TRIALING,
        current_period_start=now, current_period_end=now + timedelta(days=30),
    )
    db_session.add(sub)
    db_session.commit()

    run = service.run_zoikonex_reconciliation(db_session)

    assert run.unsynced_subscriptions == 1
    assert run.exceptions_found == 1

    exceptions = service.list_zoikonex_reconciliation_exceptions(db_session, resolved=False)
    matching = [e for e in exceptions if e.subject_id == sub.id]
    assert len(matching) == 1
    assert matching[0].exception_type == ZoikoNexReconciliationExceptionType.SUBSCRIPTION_MISSING_ZOIKONEX_REF


def test_reconciliation_rerun_does_not_duplicate_already_open_exceptions(db_session):
    from app.usage.models import UsageEvent

    service.run_zoikonex_reconciliation(db_session)  # absorb pre-existing drift, see comment above

    account = _make_account(db_session, "Rerun Drift Co")
    event = UsageEvent(
        account_id=account.id, event_type="call_seconds", quantity=10, unit="seconds",
        country_band=None, idempotency_key="zn-recon-rerun-1",
    )
    db_session.add(event)
    db_session.commit()

    first_run = service.run_zoikonex_reconciliation(db_session)
    assert first_run.exceptions_found == 1

    second_run = service.run_zoikonex_reconciliation(db_session)
    assert second_run.exceptions_found == 0  # same drift, still open - not a new exception
    assert second_run.unsynced_usage_events == 1  # but still reported as currently out of sync

    open_exceptions = [
        e for e in service.list_zoikonex_reconciliation_exceptions(db_session, resolved=False)
        if e.subject_id == event.id
    ]
    assert len(open_exceptions) == 1


def test_resolve_reconciliation_exception_requires_super_admin(client, db_session):
    from app.usage.models import UsageEvent

    account = _make_account(db_session, "Resolve Auth Co")
    event = UsageEvent(
        account_id=account.id, event_type="call_seconds", quantity=10, unit="seconds",
        country_band=None, idempotency_key="zn-recon-resolve-auth-1",
    )
    db_session.add(event)
    db_session.commit()
    run = service.run_zoikonex_reconciliation(db_session)
    exception_id = [e for e in service.list_zoikonex_reconciliation_exceptions(db_session) if e.run_id == run.id][0].id

    support_token = _create_and_login_staff(db_session, client, "zoikonexrecon1@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    response = client.post(
        f"/billing/zoikonex/reconciliation/exceptions/{exception_id}/resolve",
        json={"reason": "investigated, harmless test artifact"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_resolve_reconciliation_exception_succeeds_for_super_admin(client, db_session):
    from app.usage.models import UsageEvent

    account = _make_account(db_session, "Resolve Success Co")
    event = UsageEvent(
        account_id=account.id, event_type="call_seconds", quantity=10, unit="seconds",
        country_band=None, idempotency_key="zn-recon-resolve-success-1",
    )
    db_session.add(event)
    db_session.commit()
    run = service.run_zoikonex_reconciliation(db_session)
    exception_id = [e for e in service.list_zoikonex_reconciliation_exceptions(db_session) if e.run_id == run.id][0].id

    admin_token = _create_and_login_staff(db_session, client, "zoikonexrecon2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    response = client.post(
        f"/billing/zoikonex/reconciliation/exceptions/{exception_id}/resolve",
        json={"reason": "confirmed test artifact, ignoring"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["resolved_by"] is not None
    assert body["resolution_reason"] == "confirmed test artifact, ignoring"

    unresolved = client.get(
        "/billing/zoikonex/reconciliation/exceptions", params={"resolved": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    assert exception_id not in [e["id"] for e in unresolved]

    resolved = client.get(
        "/billing/zoikonex/reconciliation/exceptions", params={"resolved": True},
        headers={"Authorization": f"Bearer {admin_token}"},
    ).json()
    assert exception_id in [e["id"] for e in resolved]


def test_resolve_reconciliation_exception_returns_404_for_unknown_id(client, db_session):
    admin_token = _create_and_login_staff(db_session, client, "zoikonexrecon3@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN)
    response = client.post(
        "/billing/zoikonex/reconciliation/exceptions/00000000-0000-0000-0000-000000000000/resolve",
        json={"reason": "no such exception"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


def test_run_reconciliation_route_accessible_to_any_staff_role(client, db_session):
    account = _make_account(db_session, "Run Route Co")
    service.get_or_create_subscription(db_session, account.id)

    token = _create_and_login_staff(db_session, client, "zoikonexrecon4@zoikolocal.com", role=PlatformStaffRole.SUPPORT)
    response = client.post("/billing/zoikonex/reconciliation/run", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert "exceptions_found" in body

    list_response = client.get("/billing/zoikonex/reconciliation/runs", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    assert any(run["id"] == body["id"] for run in list_response.json())
