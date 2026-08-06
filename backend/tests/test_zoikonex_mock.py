from datetime import datetime, timedelta, timezone

from app.billing import service
from app.billing.models import Subscription, SubscriptionStatus, ZoikoNexSyncEvent, ZoikoNexSyncEventType
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


# --- Sync mechanics ---


def test_new_subscription_is_synced_to_zoikonex_on_creation(db_session):
    account = _make_account(db_session, "Sync New Sub Co")
    sub = service.get_or_create_subscription(db_session, account.id)

    assert sub.zoikonex_ref is not None
    assert sub.zoikonex_ref.startswith("zn_sub_")

    events = db_session.query(ZoikoNexSyncEvent).filter(ZoikoNexSyncEvent.account_id == account.id).all()
    assert len(events) == 1
    assert events[0].event_type == ZoikoNexSyncEventType.SUBSCRIPTION_SYNC


def test_change_plan_re_syncs_to_zoikonex(db_session):
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


def test_recording_usage_syncs_it_to_zoikonex(client, db_session):
    from app.usage.service import record_usage_event

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
    assert events[0].zoikonex_ref.startswith("zn_usage_")


def test_duplicate_usage_event_is_not_synced_twice(db_session):
    from app.usage.service import record_usage_event

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
