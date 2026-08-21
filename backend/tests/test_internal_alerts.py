from app.audit.models import AuditEvent
from app.notifications.service import NotificationTemplateMissingError, send_internal_alert
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _stub_send_email(monkeypatch, *, raise_for: set[str] | None = None):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "fake-key-for-test")
    sent = []

    def _fake_send(**kwargs):
        if raise_for and kwargs["to"] in raise_for:
            from app.integrations.notifications.email import EmailError

            raise EmailError("simulated provider failure")
        sent.append(kwargs)
        return "resend_msg_1"

    monkeypatch.setattr("app.notifications.service.send_email", _fake_send)
    return sent


def test_internal_alert_only_reaches_super_admins(client, db_session, monkeypatch):
    # This dev DB is shared and already has other real SUPER_ADMIN staff
    # seeded from prior acceptance/smoke runs (see CLAUDE.md's standing
    # note on expecting exactly this) - assertions below check "my new
    # staff member is included" and "the SUPPORT one is excluded", not an
    # exact recipient set, so they hold regardless of what else is there.
    sent = _stub_send_email(monkeypatch)
    staff_service.create_staff(
        db_session, email="superadmin1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_service.create_staff(
        db_session, email="support1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )

    send_internal_alert(
        db_session, event_name="trust_int.fraud_spend_spike",
        summary="Test fraud spend spike.", console_link="https://staff.example/fraud", tenant_reference="acct-123",
    )

    recipients = {s["to"] for s in sent}
    assert "superadmin1@zoikolocal.com" in recipients
    assert "support1@zoikolocal.com" not in recipients
    mine = next(s for s in sent if s["to"] == "superadmin1@zoikolocal.com")
    assert "[SEV-1] Calling fraud spend spike — acct-123" in mine["subject"]
    assert "Test fraud spend spike." in mine["body"]
    assert "https://staff.example/fraud" in mine["body"]


def test_internal_alert_rejects_an_unknown_event_name(db_session):
    try:
        send_internal_alert(db_session, event_name="not_a_real_event", summary="x")
        assert False, "expected NotificationTemplateMissingError"
    except NotificationTemplateMissingError:
        pass


def test_internal_alert_is_audited(client, db_session, monkeypatch):
    _stub_send_email(monkeypatch)
    staff_service.create_staff(
        db_session, email="superadmin2@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )

    send_internal_alert(db_session, event_name="ops_int.communications_backlog", summary="Queue backed up.")

    # before_hash/after_hash are one-way hashes (see AuditEvent's
    # docstring), not the raw dict - actor/action/target are the real,
    # directly checkable evidence that this specific alert was audited.
    event = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "internal_alert.sent", AuditEvent.actor == "system")
        .order_by(AuditEvent.created_at.desc())
        .first()
    )
    assert event is not None
    assert event.after_hash is not None


def test_one_bad_super_admin_mailbox_does_not_block_the_others(client, db_session, monkeypatch):
    sent = _stub_send_email(monkeypatch, raise_for={"bouncing@zoikolocal.com"})
    staff_service.create_staff(
        db_session, email="bouncing@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )
    staff_service.create_staff(
        db_session, email="working@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )

    # Must not raise, even though one of the two recipients fails.
    send_internal_alert(db_session, event_name="ops_int.communications_backlog", summary="Queue backed up.")

    recipients = {s["to"] for s in sent}
    assert "working@zoikolocal.com" in recipients
    assert "bouncing@zoikolocal.com" not in recipients


def test_reconciliation_run_alerts_staff_when_it_finds_a_real_exception(client, db_session, monkeypatch):
    from app.billing import service as billing_service
    from app.billing.models import Subscription, SubscriptionStatus
    from app.numbering.identity.models import Account, AccountType
    from datetime import datetime, timedelta, timezone

    sent = _stub_send_email(monkeypatch)
    staff_service.create_staff(
        db_session, email="superadmin3@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )

    account = Account(name="Drift Co", account_type=AccountType.BUSINESS)
    db_session.add(account)
    db_session.flush()
    now = datetime.now(timezone.utc)
    # Bypasses get_or_create_subscription (and sync_subscription_to_zoikonex)
    # entirely - zoikonex_ref stays NULL, which is exactly the drift
    # run_zoikonex_reconciliation is supposed to catch.
    sub = Subscription(
        account_id=account.id, plan_code="free_trial", status=SubscriptionStatus.TRIALING,
        current_period_start=now, current_period_end=now + timedelta(days=30),
    )
    db_session.add(sub)
    db_session.commit()

    run = billing_service.run_zoikonex_reconciliation(db_session)

    alert_emails = [s for s in sent if "reconciliation exception" in s["subject"].lower()]
    assert len(alert_emails) > 0
    assert any(e["to"] == "superadmin3@zoikolocal.com" for e in alert_emails)
    assert run.id in alert_emails[0]["subject"]


def test_auto_suspend_alerts_staff(client, db_session, monkeypatch):
    from app.numbering.identity.models import Account, AccountType
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus
    from app.numbering.numbers.service import suspend_numbers_for_account_by_system

    sent = _stub_send_email(monkeypatch)
    staff_service.create_staff(
        db_session, email="superadmin4@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN
    )

    account = Account(name="Fraud Co", account_type=AccountType.BUSINESS)
    db_session.add(account)
    db_session.flush()
    number = PhoneNumber(e164="+15550099999", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account.id)
    db_session.add(number)
    db_session.commit()

    suspend_numbers_for_account_by_system(db_session, account.id, reason="velocity threshold exceeded")

    alert_emails = [s for s in sent if "fraud spend spike" in s["subject"].lower() and account.id in s["subject"]]
    assert len(alert_emails) > 0
    assert any(e["to"] == "superadmin4@zoikolocal.com" for e in alert_emails)
    assert account.id in alert_emails[0]["body"]
