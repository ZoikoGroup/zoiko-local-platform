import logging

from app.core.config import settings
from app.notifications.service import (
    SmsTemplateMissingError,
    notify_api_client_created,
    notify_compliance_case_approved,
    notify_compliance_case_rejected,
    notify_integration_installed,
    notify_integration_removed,
    notify_number_activated,
    notify_number_suspended,
    notify_team_member_added,
    notify_webhook_endpoint_added,
    send_sms_notification,
)


def test_notify_number_activated_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_number_activated(
            db_session, account_id=None, account_email="owner@example.com", e164="+15550001111",
            organization_name="Acme Inc",
        )
    assert any("+15550001111" in record.message for record in caplog.records)


def test_notify_number_suspended_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_number_suspended(
            db_session, account_id=None, account_email="owner@example.com", e164="+15550001111", reason="Non-payment"
        )
    assert any("suspended" in record.message and "Non-payment" in record.message for record in caplog.records)


def test_notify_compliance_case_approved_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_compliance_case_approved(
            db_session, account_id=None, account_email="owner@example.com", jurisdiction="US",
            requirement_type="kyc_individual",
        )
    assert any("approved" in record.message for record in caplog.records)


def test_notify_compliance_case_rejected_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_compliance_case_rejected(
            db_session, account_id=None, account_email="owner@example.com", jurisdiction="US",
            requirement_type="kyc_individual", reason="Blurry photo",
        )
    assert any("Blurry photo" in record.message for record in caplog.records)


def test_notify_team_member_added_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_team_member_added(
            db_session, account_id=None, member_email="member@example.com", account_name="Acme Inc", role="admin"
        )
    assert any("Acme Inc" in record.message for record in caplog.records)


def test_notify_api_client_created_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_api_client_created(
            db_session, account_id=None, account_email="owner@example.com", label="My Server",
            actor_display_name="owner@example.com",
        )
    assert any("My Server" in record.message for record in caplog.records)


def test_notify_webhook_endpoint_added_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_webhook_endpoint_added(
            db_session, account_id=None, account_email="owner@example.com", url="https://example.com/hook",
            actor_display_name="owner@example.com",
        )
    assert any("https://example.com/hook" in record.message for record in caplog.records)


def test_notify_integration_installed_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_integration_installed(
            db_session, account_id=None, account_email="owner@example.com", integration_name="Hubspot CRM",
            organization_name="Acme Inc", actor_display_name="owner@example.com",
        )
    assert any("Hubspot CRM" in record.message for record in caplog.records)


def test_notify_integration_removed_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_integration_removed(
            db_session, account_id=None, account_email="owner@example.com", integration_name="Hubspot CRM",
            organization_name="Acme Inc",
        )
    assert any("Hubspot CRM" in record.message for record in caplog.records)


def test_send_email_raises_cleanly_on_resend_failure(monkeypatch):
    import httpx

    from app.integrations.notifications.email import EmailError, send_email

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_invalid_key_for_test")
    # This test exercises the real-send Resend error path directly, which
    # the environment-isolation guard (A-46) would otherwise short-circuit
    # to log-mode in the default "development" test environment.
    monkeypatch.setattr("app.core.config.settings.environment", "production")

    def _raise(*args, **kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr("app.integrations.notifications.email.httpx.post", _raise)

    try:
        send_email(to="x@example.com", subject="test", body="test")
        assert False, "expected EmailError"
    except EmailError:
        pass


def test_notification_missing_template_raises(db_session):
    from app.notifications.service import NotificationTemplateMissingError, send_notification

    try:
        send_notification(
            db_session, event_name="does.not.exist", recipient_email="x@example.com", context={}
        )
        assert False, "expected NotificationTemplateMissingError"
    except NotificationTemplateMissingError:
        pass


def test_send_sms_notification_sends_via_twilio_when_configured(db_session, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.notifications.service.send_sms",
        lambda to, body: sent.append((to, body)) or {"sid": "SMfake", "status": "queued"},
    )

    delivery = send_sms_notification(
        db_session,
        event_name="number.suspended",
        recipient_phone="+15551234567",
        context={"e164": "+15550001111", "reason_line": " Reason: Non-payment"},
    )

    assert delivery.status == "sent"
    assert delivery.channel == "sms"
    assert delivery.recipient_phone == "+15551234567"
    assert sent == [("+15551234567", "Zoiko Local: +15550001111 has been suspended. Reason: Non-payment")]


def test_send_sms_notification_raises_for_an_email_only_template(db_session):
    try:
        send_sms_notification(
            db_session, event_name="number.activated", recipient_phone="+15551234567", context={"e164": "+1555"}
        )
        assert False, "expected SmsTemplateMissingError"
    except SmsTemplateMissingError:
        pass


def test_send_sms_notification_records_failure_without_raising(db_session, monkeypatch):
    from app.integrations.telecom.twilio import TelecomError

    def _raise(to, body):
        raise TelecomError("no notification number configured")

    monkeypatch.setattr("app.notifications.service.send_sms", _raise)

    delivery = send_sms_notification(
        db_session, event_name="number.suspended", recipient_phone="+15551234567",
        context={"e164": "+15550001111", "reason_line": ""},
    )
    assert delivery.status == "failed"
    assert delivery.error == "no notification number configured"


def test_notify_number_suspended_also_sends_sms_when_phone_provided(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr(
        "app.notifications.service.send_sms",
        lambda to, body: sent.append((to, body)) or {"sid": "SMfake", "status": "queued"},
    )

    notify_number_suspended(
        db_session, account_id=None, account_email="owner@example.com", e164="+15550001111",
        reason="Non-payment", account_phone="+15551234567",
    )

    assert len(sent) == 1
    assert sent[0][0] == "+15551234567"


def test_notify_number_suspended_without_a_phone_number_only_sends_email(db_session, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_sms", lambda to, body: sent.append((to, body)))

    notify_number_suspended(
        db_session, account_id=None, account_email="owner@example.com", e164="+15550001111", reason="Non-payment",
    )

    assert sent == []


def test_notification_delivery_is_recorded_and_listable(client, db_session, monkeypatch):
    from app.notifications.service import list_account_notifications, send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Notify Test Co",
            "account_type": "business",
            "email": "notifylist@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": "notifylist@example.com", "password": "supersecret123"})
    token = login.json()["access_token"]

    send_notification(
        db_session,
        event_name="number.activated",
        account_id=account_id,
        recipient_email="notifylist@example.com",
        context={
            "e164": "+15550009999", "number_formatted": "+15550009999",
            "organization_name": "Notify Test Co", "user_display_name": "notifylist@example.com",
        },
    )

    # Two deliveries now: signup itself sends auth.account_activated, plus
    # the number.activated sent explicitly above - the account's
    # notification list is account-wide, not filtered to one event.
    deliveries = list_account_notifications(db_session, account_id)
    assert len(deliveries) == 2
    number_activated = next(d for d in deliveries if d.event_name == "number.activated")
    assert number_activated.status == "sent"

    response = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    number_activated_row = next(r for r in body if r["subject"] == "+15550009999 is active on Zoiko Local")
    assert number_activated_row["channel"] == "email"
    assert number_activated_row["read_at"] is None


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Notify Read Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def test_mark_notification_read(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, account_id = _signup_and_login(client, "notifyread1@example.com")
    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="notifyread1@example.com",
        context={
            "e164": "+15550001234", "number_formatted": "+15550001234",
            "organization_name": "Notify Read Test Co", "user_display_name": "notifyread1@example.com",
        },
    )

    response = client.post(
        f"/notifications/{delivery.id}/read", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["read_at"] is not None

    listed = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"}).json()
    marked = next(r for r in listed if r["id"] == delivery.id)
    assert marked["read_at"] is not None


def test_mark_notification_read_rejects_other_account(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    _, owner_account_id = _signup_and_login(client, "notifyread2owner@example.com")
    delivery = send_notification(
        db_session, event_name="number.activated", account_id=owner_account_id,
        recipient_email="notifyread2owner@example.com",
        context={
            "e164": "+15550005678", "number_formatted": "+15550005678",
            "organization_name": "Notify Read Test Co", "user_display_name": "notifyread2owner@example.com",
        },
    )

    intruder_token, _ = _signup_and_login(client, "notifyread2intruder@example.com")
    response = client.post(
        f"/notifications/{delivery.id}/read", headers={"Authorization": f"Bearer {intruder_token}"}
    )
    assert response.status_code == 403


def test_mark_all_notifications_read(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, account_id = _signup_and_login(client, "notifyreadall@example.com")
    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="notifyreadall@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Notify Read Test Co", "user_display_name": "notifyreadall@example.com",
        },
    )
    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="notifyreadall@example.com",
        context={
            "e164": "+15550002222", "number_formatted": "+15550002222",
            "organization_name": "Notify Read Test Co", "user_display_name": "notifyreadall@example.com",
        },
    )

    # 3, not 2 - signup itself sends auth.account_activated in addition to
    # the two number.activated notifications sent explicitly above.
    response = client.post("/notifications/read-all", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["marked_read"] == 3

    listed = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert all(n["read_at"] is not None for n in listed)


def test_mark_notification_read_requires_auth(client):
    response = client.post("/notifications/some-id/read")
    assert response.status_code == 401


def test_push_subscribe_then_unsubscribe(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, _ = _signup_and_login(client, "pushsubscribe@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/notifications/push/subscribe", headers=headers,
        json={"endpoint": "https://push.example.com/abc123", "p256dh": "fake-p256dh", "auth": "fake-auth"},
    )
    assert response.status_code == 200
    assert response.json()["endpoint"] == "https://push.example.com/abc123"

    unsub = client.post(
        "/notifications/push/unsubscribe", headers=headers, json={"endpoint": "https://push.example.com/abc123"},
    )
    assert unsub.status_code == 204


def test_push_subscribe_upserts_on_same_endpoint(db_session, client, monkeypatch):
    from app.notifications.models import PushSubscription
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    token, _ = _signup_and_login(client, "pushupsert@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for _ in range(2):
        response = client.post(
            "/notifications/push/subscribe", headers=headers,
            json={"endpoint": "https://push.example.com/same", "p256dh": "key1", "auth": "auth1"},
        )
        assert response.status_code == 200

    count = db_session.query(PushSubscription).filter(PushSubscription.endpoint == "https://push.example.com/same").count()
    assert count == 1


def test_send_notification_fans_out_to_subscribed_push_devices(db_session, monkeypatch):
    from app.core.security import hash_password
    from app.notifications.models import NotificationDelivery
    from app.notifications.service import send_notification, subscribe_to_push
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr(
        "app.notifications.service.send_push",
        lambda endpoint, p256dh, auth, title, body: sent.append((endpoint, title, body)),
    )

    account = Account(name="Push Fanout Co", account_type=AccountType.BUSINESS)
    db_session.add(account)
    db_session.flush()
    user = User(
        account_id=account.id, email="pushfanout@example.com", hashed_password=hash_password("supersecret123"),
        role=UserRole.OWNER,
    )
    db_session.add(user)
    db_session.commit()

    subscribe_to_push(
        db_session, account_id=account.id, user_id=user.id,
        endpoint="https://push.example.com/fanout", p256dh="k", auth="a",
    )

    send_notification(
        db_session, event_name="number.activated", account_id=account.id,
        recipient_email="pushfanout@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Push Fanout Co", "user_display_name": "pushfanout@example.com",
        },
    )

    assert sent == [("https://push.example.com/fanout", "+15550001111 is active on Zoiko Local",
                      "Your number is active\n\nHello pushfanout@example.com, +15550001111 is now active for "
                      "Push Fanout Co. Confirm its inbound route, outbound caller ID rules, emergency address "
                      "where applicable, voicemail, hours, and failover destination.\n\nNext: Configure Active Number.")]

    push_delivery = (
        db_session.query(NotificationDelivery)
        .filter(NotificationDelivery.channel == "push", NotificationDelivery.account_id == account.id)
        .first()
    )
    assert push_delivery is not None
    assert push_delivery.status == "sent"


def test_push_fan_out_removes_expired_subscription(db_session, monkeypatch):
    from app.integrations.notifications.webpush import PushSubscriptionExpiredError
    from app.notifications.models import PushSubscription
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    def _raise(**kwargs):
        raise PushSubscriptionExpiredError("gone")

    monkeypatch.setattr("app.notifications.service.send_push", _raise)

    signup_email = "pushexpired@example.com"
    from app.numbering.identity.models import Account, AccountType, User, UserRole
    from app.core.security import hash_password

    account = Account(name="Push Expired Co", account_type=AccountType.BUSINESS)
    db_session.add(account)
    db_session.flush()
    user = User(
        account_id=account.id, email=signup_email, hashed_password=hash_password("supersecret123"),
        role=UserRole.OWNER,
    )
    db_session.add(user)
    db_session.commit()

    subscription = PushSubscription(
        account_id=account.id, user_id=user.id, endpoint="https://push.example.com/expired",
        p256dh="k", auth="a",
    )
    db_session.add(subscription)
    db_session.commit()

    send_notification(
        db_session, event_name="number.activated", account_id=account.id,
        recipient_email=signup_email,
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Push Expired Co", "user_display_name": signup_email,
        },
    )

    remaining = (
        db_session.query(PushSubscription)
        .filter(PushSubscription.endpoint == "https://push.example.com/expired")
        .count()
    )
    assert remaining == 0


def test_notifications_list_endpoint_serializes_push_deliveries_with_no_email(client, db_session, monkeypatch):
    """Regression test: NotificationDeliveryResponse used to require
    recipient_email as a non-nullable str, but SMS/push deliveries have it
    null - that mismatch would 500 the whole /notifications/me list (and
    therefore the notification bell) for any account with a non-email
    delivery in its history."""
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.notifications.service.send_push", lambda **kwargs: None)

    token, account_id = _signup_and_login(client, "pushlistregression@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    subscribe_response = client.post(
        "/notifications/push/subscribe", headers=headers,
        json={"endpoint": "https://push.example.com/listregression", "p256dh": "k", "auth": "a"},
    )
    assert subscribe_response.status_code == 200

    from app.notifications.service import send_notification
    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="pushlistregression@example.com",
        context={
            "e164": "+15550009000", "number_formatted": "+15550009000",
            "organization_name": "Notify Read Test Co", "user_display_name": "pushlistregression@example.com",
        },
    )

    response = client.get("/notifications/me", headers=headers)
    assert response.status_code == 200
    channels = {row["channel"] for row in response.json()}
    assert channels == {"email", "push"}
    push_row = next(row for row in response.json() if row["channel"] == "push")
    assert push_row["recipient_email"] is None


# --- Priority tiers, quiet hours, and the preference/suppression center ---


def test_get_or_create_preference_returns_sane_defaults(db_session):
    from app.notifications.service import get_or_create_preference

    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Prefs Default Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    pref = get_or_create_preference(db_session, account.id)
    assert pref.transactional_enabled is True
    assert pref.sms_enabled is True
    assert pref.quiet_hours_start is None
    assert pref.quiet_hours_timezone == "UTC"

    # Idempotent - a second call returns the same row, not a duplicate.
    again = get_or_create_preference(db_session, account.id)
    assert again.account_id == pref.account_id


def test_update_preference_sets_and_clears_quiet_hours(db_session):
    from datetime import time as dtime

    from app.notifications.service import update_preference

    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Prefs Update Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    pref = update_preference(
        db_session, account.id,
        quiet_hours_start=dtime(22, 0), quiet_hours_end=dtime(7, 0), quiet_hours_timezone="America/New_York",
    )
    assert pref.quiet_hours_start == dtime(22, 0)
    assert pref.quiet_hours_timezone == "America/New_York"

    cleared = update_preference(db_session, account.id, quiet_hours_start=None, quiet_hours_end=None)
    assert cleared.quiet_hours_start is None
    assert cleared.quiet_hours_end is None
    # Not passed this time - must stay unchanged (the `...` sentinel), not reset to UTC.
    assert cleared.quiet_hours_timezone == "America/New_York"


def test_update_preference_rejects_an_invalid_timezone(db_session):
    from app.notifications.service import InvalidTimezoneError, update_preference

    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Prefs Badtz Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()

    try:
        update_preference(db_session, account.id, quiet_hours_timezone="Not/A_Zone")
        assert False, "expected InvalidTimezoneError"
    except InvalidTimezoneError:
        pass


def test_is_within_quiet_hours_normal_and_overnight_ranges():
    from datetime import datetime, time as dtime, timezone as dtimezone

    from app.notifications.service import is_within_quiet_hours
    from app.notifications.models import NotificationPreference

    normal = NotificationPreference(
        account_id="x", quiet_hours_start=dtime(9, 0), quiet_hours_end=dtime(17, 0), quiet_hours_timezone="UTC"
    )
    assert is_within_quiet_hours(normal, now=datetime(2026, 1, 1, 12, 0, tzinfo=dtimezone.utc)) is True
    assert is_within_quiet_hours(normal, now=datetime(2026, 1, 1, 20, 0, tzinfo=dtimezone.utc)) is False

    overnight = NotificationPreference(
        account_id="x", quiet_hours_start=dtime(22, 0), quiet_hours_end=dtime(7, 0), quiet_hours_timezone="UTC"
    )
    assert is_within_quiet_hours(overnight, now=datetime(2026, 1, 1, 23, 0, tzinfo=dtimezone.utc)) is True
    assert is_within_quiet_hours(overnight, now=datetime(2026, 1, 1, 3, 0, tzinfo=dtimezone.utc)) is True
    assert is_within_quiet_hours(overnight, now=datetime(2026, 1, 1, 12, 0, tzinfo=dtimezone.utc)) is False

    disabled = NotificationPreference(account_id="x", quiet_hours_timezone="UTC")
    assert is_within_quiet_hours(disabled, now=datetime(2026, 1, 1, 23, 0, tzinfo=dtimezone.utc)) is False


def test_transactional_email_is_suppressed_when_opted_out(db_session, monkeypatch):
    from app.notifications.service import send_notification, update_preference

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="Suppress Email Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    update_preference(db_session, account.id, transactional_enabled=False)

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account.id,
        recipient_email="x@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Suppress Email Co", "user_display_name": "x@example.com",
        },
    )
    assert delivery.status == "suppressed"
    assert sent == []


def test_critical_priority_email_bypasses_transactional_suppression(db_session, monkeypatch):
    from app.notifications.service import send_notification, update_preference

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="Critical Bypass Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    update_preference(db_session, account.id, transactional_enabled=False)

    # number.suspended is CRITICAL priority despite being TRANSACTIONAL category.
    delivery = send_notification(
        db_session, event_name="number.suspended", account_id=account.id,
        recipient_email="x@example.com", context={"e164": "+15550001111", "reason_line": ""},
    )
    assert delivery.status == "sent"
    assert len(sent) == 1


def test_security_category_email_bypasses_transactional_suppression(db_session, monkeypatch):
    from app.notifications.service import send_notification, update_preference

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="Security Bypass Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    update_preference(db_session, account.id, transactional_enabled=False)

    delivery = send_notification(
        db_session, event_name="team_member.added", account_id=account.id,
        recipient_email="x@example.com", context={"account_name": "Acme", "role": "admin"},
    )
    assert delivery.status == "sent"
    assert len(sent) == 1


def test_transactional_email_includes_rfc8058_unsubscribe_headers(db_session, monkeypatch):
    from app.notifications.service import send_notification

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="RFC8058 Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    db_session.commit()

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account.id,
        recipient_email="x@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "RFC8058 Co", "user_display_name": "x@example.com",
        },
    )
    assert delivery.status == "sent"
    assert len(sent) == 1
    headers = sent[0]["headers"]
    assert headers["List-Unsubscribe"].startswith("<")
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_critical_email_has_no_unsubscribe_headers(db_session, monkeypatch):
    """CRITICAL/SECURITY templates never get an unsubscribe path at all -
    same exemption as the transactional opt-out, not just a suppression
    bypass - so there's no List-Unsubscribe header to send either."""
    from app.notifications.service import send_notification

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="Critical Headers Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    db_session.commit()

    delivery = send_notification(
        db_session, event_name="number.suspended", account_id=account.id,
        recipient_email="x@example.com", context={"e164": "+15550001111", "reason_line": ""},
    )
    assert delivery.status == "sent"
    assert sent[0]["headers"] is None


def test_transactional_email_sends_styled_html_alongside_plain_text(db_session, monkeypatch):
    """Real gap fixed 2026-08-22: every email went out as unstyled plain
    text, including security emails - the Email Communications System doc
    treats this as a launch blocker. This proves send_notification now
    passes a real HTML alternative through to send_email, with a genuine
    clickable unsubscribe link embedded in it (not just plain-text URL)."""
    from app.notifications.service import send_notification

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="HTML Email Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    db_session.commit()

    delivery = send_notification(
        db_session, event_name="number.activated", account_id=account.id,
        recipient_email="x@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "HTML Email Co", "user_display_name": "x@example.com",
        },
    )
    assert delivery.status == "sent"
    html = sent[0]["html"]
    assert html is not None
    assert "<html>" in html
    assert sent[0]["subject"] in html
    assert '<a href="' in html
    assert "Unsubscribe" in html


def test_critical_email_html_has_no_unsubscribe_link(db_session, monkeypatch):
    from app.notifications.service import send_notification

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="Critical HTML Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    db_session.commit()

    delivery = send_notification(
        db_session, event_name="number.suspended", account_id=account.id,
        recipient_email="x@example.com", context={"e164": "+15550001111", "reason_line": ""},
    )
    assert delivery.status == "sent"
    assert "Unsubscribe" not in sent[0]["html"]


def test_render_html_email_escapes_untrusted_body_content():
    """context values (e.g. an account name) flow into the email body
    unescaped in the plain-text template - the HTML render must escape
    them itself rather than trust the caller, since a malicious account
    name like an org name containing markup must not become live HTML in
    a rendered email."""
    from app.notifications.service import _render_html_email

    html = _render_html_email("Test subject", "Hello <script>alert(1)</script> friend")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_resolve_from_address_defaults_to_single_address_until_domain_verified(monkeypatch):
    """Real gap fixed 2026-08-22: all email shared one sending address/
    domain, risking marketing mail's reputation dragging down password-
    reset deliverability. zoikolocal.com is the real domain (founder,
    2026-08-22) but its Resend DNS verification hasn't been confirmed yet
    - the per-category mapping must stay inert (fall back to today's
    single working address) until email_domain_verified is flipped on, so
    wiring this in never itself causes emails to start bouncing."""
    from app.notifications.service import _resolve_from_address

    monkeypatch.setattr("app.core.config.settings.email_domain_verified", False)
    assert _resolve_from_address("AUTH") == settings.email_from_address
    assert _resolve_from_address("BILL") == settings.email_from_address
    assert _resolve_from_address("MKTG") == settings.email_from_address
    assert _resolve_from_address(None) == settings.email_from_address


def test_resolve_from_address_routes_by_category_once_domain_verified(monkeypatch):
    from app.notifications.service import _resolve_from_address

    monkeypatch.setattr("app.core.config.settings.email_domain_verified", True)
    assert _resolve_from_address("AUTH") == settings.email_from_address_security
    assert _resolve_from_address("TRUST") == settings.email_from_address_security
    assert _resolve_from_address("COMP") == settings.email_from_address_security
    assert _resolve_from_address("DEVICE") == settings.email_from_address_security
    assert _resolve_from_address("BILL") == settings.email_from_address_billing
    assert _resolve_from_address("MKTG") == settings.email_from_address_marketing
    assert _resolve_from_address("PART") == settings.email_from_address_marketing
    # Unmapped/unknown domain values fall through to "notifications" rather
    # than raising - a missing mapping should never be why an email fails.
    assert _resolve_from_address("NUM") == settings.email_from_address_notifications
    assert _resolve_from_address(None) == settings.email_from_address_notifications


def test_send_notification_uses_billing_address_once_domain_verified(db_session, monkeypatch):
    from app.notifications.service import send_notification

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.core.config.settings.email_domain_verified", True)
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    account = Account(name="Billing Domain Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    db_session.commit()

    delivery = send_notification(
        db_session, event_name="billing.plan_started", account_id=account.id,
        recipient_email="x@example.com",
        context={
            "user_display_name": "x@example.com", "organization_name": "Billing Domain Co",
            "subscription_plan_name": "Pro", "subscription_billing_interval": "monthly",
            "subscription_next_billing_date": "2026-09-22",
        },
    )
    assert delivery.status == "sent"
    assert sent[0]["from_address"] == settings.email_from_address_billing


def test_email_is_held_during_quiet_hours(db_session, monkeypatch):
    from datetime import time as dtime

    from app.notifications.service import send_notification, update_preference
    from app.notifications.models import NotificationTemplate, NotificationPriority

    from app.numbering.identity.models import Account, AccountType

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    sent = []
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: sent.append(kw))

    template = db_session.query(NotificationTemplate).filter(NotificationTemplate.key == "number.suspended").first()
    template.priority = NotificationPriority.STANDARD
    db_session.commit()

    try:
        account = Account(name="Quiet Hours Email Co", account_type=AccountType.INDIVIDUAL)
        db_session.add(account)
        db_session.flush()
        update_preference(
            db_session, account.id,
            quiet_hours_start=dtime(0, 0, 0), quiet_hours_end=dtime(23, 59, 59), quiet_hours_timezone="UTC",
        )

        delivery = send_notification(
            db_session, event_name="number.suspended", account_id=account.id,
            recipient_email="x@example.com", context={"e164": "+15550001111", "reason_line": ""},
        )
        assert delivery.status == "suppressed"
        assert "quiet hours" in delivery.error
        assert sent == []
    finally:
        # This test file's db_session isn't rolled back between tests (see
        # the identical pre-existing pattern in
        # test_sms_is_suppressed_for_a_standard_priority_template_when_disabled) -
        # a mutation to this shared, canonical template row must be undone
        # here or every later test relying on number.suspended being
        # CRITICAL breaks, regardless of what order tests actually run in.
        template.priority = NotificationPriority.CRITICAL
        db_session.commit()


def test_push_is_suppressed_when_opted_out(db_session, monkeypatch):
    from app.notifications.service import send_notification, update_preference
    from app.notifications.models import PushSubscription

    from app.core.security import hash_password
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: None)
    pushed = []
    monkeypatch.setattr("app.notifications.service.send_push", lambda **kw: pushed.append(kw))

    account = Account(name="Suppress Push Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    user = User(
        account_id=account.id, email="suppresspush@example.com", hashed_password=hash_password("supersecret123"),
        role=UserRole.OWNER,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(PushSubscription(account_id=account.id, user_id=user.id, endpoint="https://push.example.com/a", p256dh="k", auth="a"))
    db_session.commit()
    update_preference(db_session, account.id, transactional_enabled=False)

    send_notification(
        db_session, event_name="number.activated", account_id=account.id,
        recipient_email="x@example.com",
        context={
            "e164": "+15550001111", "number_formatted": "+15550001111",
            "organization_name": "Suppress Push Co", "user_display_name": "x@example.com",
        },
    )
    assert pushed == []


def test_push_is_held_during_quiet_hours(db_session, monkeypatch):
    from datetime import time as dtime

    from app.notifications.service import send_notification, update_preference
    from app.notifications.models import PushSubscription, NotificationTemplate, NotificationPriority

    from app.core.security import hash_password
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: None)
    pushed = []
    monkeypatch.setattr("app.notifications.service.send_push", lambda **kw: pushed.append(kw))

    template = db_session.query(NotificationTemplate).filter(NotificationTemplate.key == "number.suspended").first()
    template.priority = NotificationPriority.STANDARD
    db_session.commit()

    try:
        account = Account(name="Quiet Hours Push Co", account_type=AccountType.INDIVIDUAL)
        db_session.add(account)
        db_session.flush()
        user = User(
            account_id=account.id, email="quiethourspush@example.com", hashed_password=hash_password("supersecret123"),
            role=UserRole.OWNER,
        )
        db_session.add(user)
        db_session.flush()
        db_session.add(PushSubscription(account_id=account.id, user_id=user.id, endpoint="https://push.example.com/b", p256dh="k", auth="a"))
        db_session.commit()
        update_preference(
            db_session, account.id,
            quiet_hours_start=dtime(0, 0, 0), quiet_hours_end=dtime(23, 59, 59), quiet_hours_timezone="UTC",
        )

        send_notification(
            db_session, event_name="number.suspended", account_id=account.id,
            recipient_email="x@example.com", context={"e164": "+15550001111", "reason_line": ""},
        )
        assert pushed == []
    finally:
        # See test_email_is_held_during_quiet_hours's finally block - same
        # shared-row-mutation-must-be-undone reasoning.
        template.priority = NotificationPriority.CRITICAL
        db_session.commit()


def test_critical_push_bypasses_suppression(db_session, monkeypatch):
    from app.notifications.service import send_notification, update_preference
    from app.notifications.models import PushSubscription

    from app.core.security import hash_password
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    monkeypatch.setattr("app.notifications.service.send_email", lambda **kw: None)
    pushed = []
    monkeypatch.setattr("app.notifications.service.send_push", lambda **kw: pushed.append(kw))

    account = Account(name="Critical Push Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    user = User(
        account_id=account.id, email="criticalpush@example.com", hashed_password=hash_password("supersecret123"),
        role=UserRole.OWNER,
    )
    db_session.add(user)
    db_session.flush()
    db_session.add(PushSubscription(account_id=account.id, user_id=user.id, endpoint="https://push.example.com/c", p256dh="k", auth="a"))
    db_session.commit()
    update_preference(db_session, account.id, transactional_enabled=False)

    # number.suspended is CRITICAL priority - bypasses the transactional opt-out for push too.
    send_notification(
        db_session, event_name="number.suspended", account_id=account.id,
        recipient_email="x@example.com", context={"e164": "+15550001111", "reason_line": ""},
    )
    assert len(pushed) == 1


def test_sms_is_suppressed_when_sms_disabled(db_session, monkeypatch):
    from app.notifications.service import send_sms_notification, update_preference

    from app.numbering.identity.models import Account, AccountType

    sent = []
    monkeypatch.setattr("app.notifications.service.send_sms", lambda to, body: sent.append((to, body)))

    account = Account(name="Suppress SMS Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    update_preference(db_session, account.id, sms_enabled=False)

    delivery = send_sms_notification(
        db_session, event_name="number.suspended", account_id=account.id,
        recipient_phone="+15551234567", context={"e164": "+15550001111", "reason_line": ""},
    )
    # number.suspended is CRITICAL, so the opt-out is bypassed here too -
    # confirms SMS suppression and CRITICAL-exemption compose correctly.
    assert delivery.status == "sent"
    assert len(sent) == 1


def test_sms_is_suppressed_for_a_standard_priority_template_when_disabled(db_session, monkeypatch):
    from app.notifications.service import send_sms_notification, update_preference
    from app.notifications.models import NotificationTemplate, NotificationCategory

    from app.numbering.identity.models import Account, AccountType

    sent = []
    monkeypatch.setattr("app.notifications.service.send_sms", lambda to, body: sent.append((to, body)))

    # number.suspended is the only seeded template with an sms_body_template -
    # temporarily drop it to STANDARD to exercise the non-exempt SMS path.
    template = db_session.query(NotificationTemplate).filter(NotificationTemplate.key == "number.suspended").first()
    from app.notifications.models import NotificationPriority
    template.priority = NotificationPriority.STANDARD
    db_session.commit()

    try:
        account = Account(name="Suppress SMS Standard Co", account_type=AccountType.INDIVIDUAL)
        db_session.add(account)
        db_session.flush()
        update_preference(db_session, account.id, sms_enabled=False)

        delivery = send_sms_notification(
            db_session, event_name="number.suspended", account_id=account.id,
            recipient_phone="+15551234567", context={"e164": "+15550001111", "reason_line": ""},
        )
        assert delivery.status == "suppressed"
        assert "disabled" in delivery.error
        assert sent == []
    finally:
        # This test's db_session isn't rolled back between tests - a
        # mutation to this shared, canonical template row must be undone
        # here or every later test relying on number.suspended being
        # CRITICAL breaks (confirmed live: this exact gap broke
        # test_critical_priority_email_bypasses_transactional_suppression
        # and others until this restore was added).
        template.priority = NotificationPriority.CRITICAL
        db_session.commit()


def test_sms_is_held_during_quiet_hours(db_session, monkeypatch):
    from datetime import time as dtime

    from app.notifications.service import send_sms_notification, update_preference
    from app.notifications.models import NotificationTemplate, NotificationPriority

    from app.numbering.identity.models import Account, AccountType

    sent = []
    monkeypatch.setattr("app.notifications.service.send_sms", lambda to, body: sent.append((to, body)))

    template = db_session.query(NotificationTemplate).filter(NotificationTemplate.key == "number.suspended").first()
    template.priority = NotificationPriority.STANDARD
    db_session.commit()

    try:
        account = Account(name="Quiet Hours Co", account_type=AccountType.INDIVIDUAL)
        db_session.add(account)
        db_session.flush()
        # Covers virtually the entire day so this is deterministic regardless of
        # wall-clock time when the test runs.
        update_preference(
            db_session, account.id,
            quiet_hours_start=dtime(0, 0, 0), quiet_hours_end=dtime(23, 59, 59), quiet_hours_timezone="UTC",
        )

        delivery = send_sms_notification(
            db_session, event_name="number.suspended", account_id=account.id,
            recipient_phone="+15551234567", context={"e164": "+15550001111", "reason_line": ""},
        )
        assert delivery.status == "suppressed"
        assert "quiet hours" in delivery.error
        assert sent == []
    finally:
        # See test_email_is_held_during_quiet_hours's finally block - same
        # shared-row-mutation-must-be-undone reasoning.
        template.priority = NotificationPriority.CRITICAL
        db_session.commit()


def _signup_and_login_owner(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Prefs Route Co", "account_type": "business", "email": email, "password": "supersecret123"},
    )
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def test_get_preferences_requires_auth(client):
    response = client.get("/notifications/preferences")
    assert response.status_code == 401


def test_get_preferences_returns_defaults(client):
    token = _signup_and_login_owner(client, "prefsroute1@example.com")
    response = client.get("/notifications/preferences", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["transactional_enabled"] is True
    assert body["sms_enabled"] is True
    assert body["quiet_hours_start"] is None


def test_put_preferences_updates_fields(client):
    token = _signup_and_login_owner(client, "prefsroute2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put(
        "/notifications/preferences",
        json={"transactional_enabled": False, "quiet_hours_start": "22:00:00", "quiet_hours_end": "07:00:00"},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["transactional_enabled"] is False
    assert body["quiet_hours_start"] == "22:00:00"

    refetched = client.get("/notifications/preferences", headers=headers).json()
    assert refetched["transactional_enabled"] is False


def test_put_preferences_rejects_invalid_timezone(client):
    token = _signup_and_login_owner(client, "prefsroute3@example.com")
    response = client.put(
        "/notifications/preferences",
        json={"quiet_hours_timezone": "Not/A_Real_Zone"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


def test_put_preferences_forbidden_for_viewer(client, db_session):
    from app.billing import service as billing_service

    owner_token = _signup_and_login_owner(client, "prefsrouteviewer@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    # Real gap fix (ZL-COM-ENT-001): adding a team member now requires
    # team.members.enabled (Business+).
    owner_account_id = client.get("/auth/me", headers=owner_headers).json()["account_id"]
    billing_service.change_plan(db_session, owner_account_id, "business", actor="test-setup")
    client.post(
        "/team/members",
        json={"email": "prefsrouteviewermember@example.com", "password": "supersecret123", "role": "viewer"},
        headers=owner_headers,
    )
    viewer_token = client.post(
        "/auth/login", json={"email": "prefsrouteviewermember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.put(
        "/notifications/preferences",
        json={"transactional_enabled": False},
        headers={"Authorization": f"Bearer {viewer_token}"},
    )
    assert response.status_code == 403

    # But read access is unrestricted, same as everything else Viewer can see.
    get_response = client.get(
        "/notifications/preferences", headers={"Authorization": f"Bearer {viewer_token}"}
    )
    assert get_response.status_code == 200


# --- Registry-only domains (DEVICE/SUP/MKTG/PART) ---
# None of these have a real trigger yet - no desk-phone/SIP feature, no
# support ticketing, no marketing tool, no partner program - so there's
# nothing to test at a call-site level. This just guards the seeded copy
# itself: right template counts, and every {token} is well-formed enough
# for str.format() to eventually render it without raising.


def test_registry_only_domains_are_seeded_with_the_expected_counts(db_session):
    from app.notifications.models import NotificationTemplate

    expected_counts = {"DEVICE": 11, "SUP": 5, "MKTG": 8, "PART": 8}
    for domain, expected in expected_counts.items():
        actual = db_session.query(NotificationTemplate).filter(NotificationTemplate.domain == domain).count()
        assert actual == expected, f"{domain}: expected {expected} templates, found {actual}"


def test_registry_only_domain_templates_have_well_formed_format_strings(db_session):
    import string

    from app.notifications.models import NotificationTemplate

    templates = (
        db_session.query(NotificationTemplate)
        .filter(NotificationTemplate.domain.in_(["DEVICE", "SUP", "MKTG", "PART"]))
        .all()
    )
    assert len(templates) == 32
    for template in templates:
        fields = {
            f[1]
            for text in (template.subject_template, template.body_template)
            for f in string.Formatter().parse(text)
            if f[1] is not None
        }
        # A dummy context covering every field discovered across both
        # strings - this only proves .format() won't raise (no stray/
        # mismatched braces from the doc-to-template conversion), not
        # that the copy itself is correct.
        context = {f: "x" for f in fields}
        template.subject_template.format(**context)
        template.body_template.format(**context)
