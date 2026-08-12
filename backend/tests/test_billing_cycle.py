from app.billing import service
from app.billing.models import ZoikoNexSyncEvent, ZoikoNexSyncEventType
from app.integrations.billing import zoikonex as zoikonex_adapter
from app.numbering.identity.models import Account, AccountType
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _make_account(db_session, name: str) -> Account:
    account = Account(name=name, account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    return account


def _create_and_login_staff(db_session, client, email: str, role=PlatformStaffRole.SUPER_ADMIN) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _synced_paid_subscription(db_session, name: str, plan_code: str = "starter"):
    account = _make_account(db_session, name)
    service.get_or_create_subscription(db_session, account.id)  # free_trial, synced via conftest's autouse stub
    sub = service.change_plan(db_session, account.id, plan_code, actor="test-actor")
    return account, sub


# --- run_billing_cycle (rating -> invoice -> payment pipeline) ---


def test_run_billing_cycle_happy_path_bills_and_captures(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Happy Co")

    result = service.run_billing_cycle(db_session, account.id, actor="test-actor")

    assert result["billed"] is True
    assert result["plan_code"] == "starter"
    assert result["amount_minor_units"] == zoikonex_adapter.TEST_PLACEHOLDER_PRICES["starter"]
    assert result["invoice_status"] == "ISSUED"
    assert result["payment_status"] == "captured"
    assert result["captured"] is True
    assert result["capture_error"] is None
    assert result["bill_cycle_closed"] is True

    invoice_events = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.INVOICE_GENERATED)
        .all()
    )
    assert len(invoice_events) == 1
    payment_events = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.PAYMENT_COLLECTED)
        .all()
    )
    assert len(payment_events) == 1


def test_run_billing_cycle_registers_plan_in_catalog_and_is_idempotent(db_session):
    """Plan is shared reference data, not rolled back per test (see the
    reconciliation tests' "baseline absorb" pattern in test_zoikonex_mock.py
    for the same shared-dev-DB caveat) - a prior test run may have already
    registered this plan_code in ZoikoNex's catalog, so this only asserts
    the IDs are present and STABLE across two calls, not that they start
    NULL - re-registering on every call would defeat register_plan_in_
    catalog's whole idempotency guard."""
    from app.billing.service import get_plan

    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Catalog Co")

    service.run_billing_cycle(db_session, account.id, actor="test-actor")
    plan = get_plan(db_session, "starter")
    assert plan.zoikonex_product_id is not None
    assert plan.zoikonex_offer_id is not None
    assert plan.zoikonex_price_rule_id is not None
    first_product_id = plan.zoikonex_product_id

    account2, _sub2 = _synced_paid_subscription(db_session, "Billing Cycle Catalog Co 2")
    service.run_billing_cycle(db_session, account2.id, actor="test-actor")
    db_session.refresh(plan)
    assert plan.zoikonex_product_id == first_product_id  # not re-registered


def test_run_billing_cycle_skips_free_trial_plan(db_session):
    account = _make_account(db_session, "Billing Cycle Free Co")
    service.get_or_create_subscription(db_session, account.id)  # defaults to free_trial

    result = service.run_billing_cycle(db_session, account.id, actor="test-actor")

    assert result["billed"] is False
    assert "reason" in result


def test_run_billing_cycle_raises_when_never_synced_to_zoikonex(db_session, monkeypatch):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Unsynced Co")

    # Simulate a subscription that changed plan but never got a real ZoikoNex
    # account_id - same posture as a ZoikoNex outage during signup.
    from app.billing.models import Subscription

    sub = db_session.query(Subscription).filter(Subscription.account_id == account.id).first()
    sub.zoikonex_account_id = None
    db_session.commit()
    monkeypatch.setattr(
        service.zoikonex_adapter, "sync_subscription",
        lambda db, sub, *, account_type: {"party_id": None, "customer_id": None, "account_id": None},
    )

    try:
        service.run_billing_cycle(db_session, account.id, actor="test-actor")
        assert False, "expected ZoikoNexBillingCycleError"
    except service.ZoikoNexBillingCycleError:
        pass


def test_run_billing_cycle_handles_capture_failure_gracefully(db_session, monkeypatch):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Capture Fail Co")

    def _fake_capture_fails(payment_intent_id):
        raise zoikonex_adapter.ZoikoNexCaptureFailedError("evidence-ledger gRPC marshaling bug (simulated in test)")

    monkeypatch.setattr(service.zoikonex_adapter, "capture_payment_intent", _fake_capture_fails)

    result = service.run_billing_cycle(db_session, account.id, actor="test-actor")

    assert result["billed"] is True
    assert result["payment_status"] == "authorised"  # not "captured"
    assert result["captured"] is False
    assert result["capture_error"] is not None


def test_run_billing_cycle_handles_bill_cycle_close_failure_gracefully(db_session, monkeypatch):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Close Fail Co")

    def _fake_close_fails(bill_cycle_id):
        raise zoikonex_adapter.ZoikoNexError("postgres.GetBillCycle: can't scan into dest[12]: cannot scan NULL into *string")

    monkeypatch.setattr(service.zoikonex_adapter, "close_bill_cycle", _fake_close_fails)

    result = service.run_billing_cycle(db_session, account.id, actor="test-actor")

    assert result["billed"] is True
    assert result["invoice_status"] == "ISSUED"  # unaffected by the close failure
    assert result["bill_cycle_closed"] is False
    assert result["bill_cycle_close_error"] is not None


def test_run_billing_cycle_includes_a_real_tax_decision_on_the_line_item(db_session, monkeypatch):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Tax Co")

    captured = {}

    def _spy_add_invoice_line_item(invoice_id, **kwargs):
        captured.update(kwargs)
        return {"line_item_id": "zn-line-item-test"}

    monkeypatch.setattr(service.zoikonex_adapter, "add_invoice_line_item", _spy_add_invoice_line_item)

    service.run_billing_cycle(db_session, account.id, actor="test-actor")

    # determine_tax_for_invoice_line's conftest fake always returns 0 (the
    # TAX_PLACEHOLDER_JURISDICTION_CODE policy is 0%% - see that constant's
    # docstring) - this asserts the VALUE actually reaches the line item
    # call, not that a specific tax amount was charged.
    assert captured["tax_amount_minor_units"] == 0


# --- Invoice corrections (credit/debit notes) and refunds ---


def test_issue_invoice_credit_note_records_sync_event(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Credit Note Co")

    result = service.issue_invoice_credit_note(
        db_session, account.id, "zn-invoice-test", amount_minor_units=500, reason="overbilled", actor="test-actor"
    )

    assert result["credit_note_id"] == "zn-credit-note-test"
    event = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.CREDIT_NOTE_ISSUED)
        .first()
    )
    assert event is not None
    assert event.payload["amount_minor_units"] == 500


def test_issue_invoice_debit_note_records_sync_event(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Debit Note Co")

    result = service.issue_invoice_debit_note(
        db_session, account.id, "zn-invoice-test", amount_minor_units=200, reason="underbilled", actor="test-actor"
    )

    assert result["debit_note_id"] == "zn-debit-note-test"
    event = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.DEBIT_NOTE_ISSUED)
        .first()
    )
    assert event is not None
    assert event.payload["amount_minor_units"] == 200


def test_refund_zoikonex_payment_records_sync_event(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Refund Co")

    result = service.refund_zoikonex_payment(
        db_session, account.id, "zn-payment-intent-test", amount_minor_units=500, reason="customer request", actor="test-actor"
    )

    assert result["refund_id"] == "zn-refund-test"
    event = (
        db_session.query(ZoikoNexSyncEvent)
        .filter(ZoikoNexSyncEvent.account_id == account.id, ZoikoNexSyncEvent.event_type == ZoikoNexSyncEventType.REFUND_ISSUED)
        .first()
    )
    assert event is not None
    assert event.payload["amount_minor_units"] == 500


def test_refund_zoikonex_payment_propagates_zoikonex_error(db_session, monkeypatch):
    """Confirmed live against the real stack: refunding a non-CAPTURED
    payment intent (capture is currently broken on ZoikoNex's own side)
    correctly fails with a 409 STATE_CONFLICT, not a crash - this asserts
    the function propagates that as ZoikoNexError rather than swallowing it,
    since a refund silently "succeeding" when it didn't would be far worse
    than a failed capture."""
    account, _sub = _synced_paid_subscription(db_session, "Refund Fail Co")

    def _fake_refund_fails(payment_intent_id, **kwargs):
        raise zoikonex_adapter.ZoikoNexError("illegal payment state transition", code="STATE_CONFLICT")

    monkeypatch.setattr(service.zoikonex_adapter, "create_refund", _fake_refund_fails)

    try:
        service.refund_zoikonex_payment(
            db_session, account.id, "zn-payment-intent-test", amount_minor_units=500, reason="x", actor="test-actor"
        )
        assert False, "expected ZoikoNexError"
    except zoikonex_adapter.ZoikoNexError:
        pass


# --- Staff route (maker-checker: request as one staff member, approve as a
# different one - see app.billing.models.BillingActionRequest's docstring) ---


def test_run_billing_cycle_route_requires_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Route Perms Co")

    support_token = _create_and_login_staff(
        db_session, client, "billingcyclestaff1@zoikolocal.com", role=PlatformStaffRole.SUPPORT
    )
    response = client.post(
        "/billing/zoikonex/run-billing-cycle/request",
        json={"account_id": account.id},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_run_billing_cycle_route_succeeds_for_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Billing Cycle Route Success Co")

    requester_token = _create_and_login_staff(
        db_session, client, "billingcyclestaff2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    requested = client.post(
        "/billing/zoikonex/run-billing-cycle/request",
        json={"account_id": account.id},
        headers={"Authorization": f"Bearer {requester_token}"},
    )
    assert requested.status_code == 201, requested.text
    assert requested.json()["status"] == "pending"
    action_id = requested.json()["id"]

    approver_token = _create_and_login_staff(
        db_session, client, "billingcyclestaff2approver@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    approved = client.post(
        f"/billing/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert approved.json()["result"]["billed"] is True


def test_billing_action_cannot_be_approved_by_its_own_requester(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Self Approval Blocked Co")

    admin_token = _create_and_login_staff(
        db_session, client, "selfapprovestaff@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    requested = client.post(
        "/billing/zoikonex/run-billing-cycle/request",
        json={"account_id": account.id},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    action_id = requested.json()["id"]

    response = client.post(
        f"/billing/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 403, response.text


def test_credit_note_route_requires_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Credit Note Route Perms Co")

    support_token = _create_and_login_staff(
        db_session, client, "creditnotestaff1@zoikolocal.com", role=PlatformStaffRole.SUPPORT
    )
    response = client.post(
        "/billing/zoikonex/credit-notes/request",
        json={"account_id": account.id, "invoice_id": "zn-invoice-test", "amount_minor_units": 500, "reason": "test"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_credit_note_route_succeeds_for_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Credit Note Route Success Co")

    requester_token = _create_and_login_staff(
        db_session, client, "creditnotestaff2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    requested = client.post(
        "/billing/zoikonex/credit-notes/request",
        json={"account_id": account.id, "invoice_id": "zn-invoice-test", "amount_minor_units": 500, "reason": "test"},
        headers={"Authorization": f"Bearer {requester_token}"},
    )
    assert requested.status_code == 201, requested.text
    action_id = requested.json()["id"]

    approver_token = _create_and_login_staff(
        db_session, client, "creditnotestaff2approver@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    approved = client.post(
        f"/billing/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["credit_note_id"] == "zn-credit-note-test"


def test_debit_note_route_requires_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Debit Note Route Perms Co")

    support_token = _create_and_login_staff(
        db_session, client, "debitnotestaff1@zoikolocal.com", role=PlatformStaffRole.SUPPORT
    )
    response = client.post(
        "/billing/zoikonex/debit-notes/request",
        json={"account_id": account.id, "invoice_id": "zn-invoice-test", "amount_minor_units": 200, "reason": "test"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_debit_note_route_succeeds_for_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Debit Note Route Success Co")

    requester_token = _create_and_login_staff(
        db_session, client, "debitnotestaff2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    requested = client.post(
        "/billing/zoikonex/debit-notes/request",
        json={"account_id": account.id, "invoice_id": "zn-invoice-test", "amount_minor_units": 200, "reason": "test"},
        headers={"Authorization": f"Bearer {requester_token}"},
    )
    assert requested.status_code == 201, requested.text
    action_id = requested.json()["id"]

    approver_token = _create_and_login_staff(
        db_session, client, "debitnotestaff2approver@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    approved = client.post(
        f"/billing/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["debit_note_id"] == "zn-debit-note-test"


def test_refund_route_requires_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Refund Route Perms Co")

    support_token = _create_and_login_staff(
        db_session, client, "refundstaff1@zoikolocal.com", role=PlatformStaffRole.SUPPORT
    )
    response = client.post(
        "/billing/zoikonex/refunds/request",
        json={"account_id": account.id, "payment_intent_id": "zn-payment-intent-test", "amount_minor_units": 500, "reason": "test"},
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 403


def test_refund_route_succeeds_for_super_admin(client, db_session):
    account, _sub = _synced_paid_subscription(db_session, "Refund Route Success Co")

    requester_token = _create_and_login_staff(
        db_session, client, "refundstaff2@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    requested = client.post(
        "/billing/zoikonex/refunds/request",
        json={"account_id": account.id, "payment_intent_id": "zn-payment-intent-test", "amount_minor_units": 500, "reason": "test"},
        headers={"Authorization": f"Bearer {requester_token}"},
    )
    assert requested.status_code == 201, requested.text
    action_id = requested.json()["id"]

    approver_token = _create_and_login_staff(
        db_session, client, "refundstaff2approver@zoikolocal.com", role=PlatformStaffRole.SUPER_ADMIN
    )
    approved = client.post(
        f"/billing/actions/{action_id}/approve",
        headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["result"]["refund_id"] == "zn-refund-test"
