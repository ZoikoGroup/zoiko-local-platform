import logging

from app.notifications.service import (
    SmsTemplateMissingError,
    notify_compliance_case_approved,
    notify_compliance_case_rejected,
    notify_number_activated,
    notify_number_suspended,
    notify_team_member_added,
    send_sms_notification,
)


def test_notify_number_activated_logs_when_no_resend_key_configured(db_session, monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_number_activated(db_session, account_id=None, account_email="owner@example.com", e164="+15550001111")
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


def test_send_email_raises_cleanly_on_resend_failure(monkeypatch):
    import httpx

    from app.integrations.notifications.email import EmailError, send_email

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_fake_invalid_key_for_test")

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
        context={"e164": "+15550009999"},
    )

    deliveries = list_account_notifications(db_session, account_id)
    assert len(deliveries) == 1
    assert deliveries[0].event_name == "number.activated"
    assert deliveries[0].status == "sent"

    response = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["subject"] == "+15550009999 is active on Zoiko Local"
    assert body[0]["read_at"] is None


def _signup_and_login(client, email):
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
    token = login.json()["access_token"]
    return account_id, token


def test_unread_count_and_mark_read_flow(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    account_id, token = _signup_and_login(client, "unreadflow@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="unreadflow@example.com", context={"e164": "+15550001234"},
    )

    count_response = client.get("/notifications/me/unread-count", headers=headers)
    assert count_response.json() == {"unread_count": 1}

    notification_id = client.get("/notifications/me", headers=headers).json()[0]["id"]
    read_response = client.post(f"/notifications/me/{notification_id}/read", headers=headers)
    assert read_response.status_code == 200
    assert read_response.json()["read_at"] is not None

    assert client.get("/notifications/me/unread-count", headers=headers).json() == {"unread_count": 0}


def test_mark_notification_read_404_for_another_accounts_notification(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    owner_account_id, _ = _signup_and_login(client, "notifowner@example.com")
    _, other_token = _signup_and_login(client, "notifintruder@example.com")

    send_notification(
        db_session, event_name="number.activated", account_id=owner_account_id,
        recipient_email="notifowner@example.com", context={"e164": "+15550005678"},
    )

    owner_headers = {"Authorization": f"Bearer {_login_token(client, 'notifowner@example.com')}"}
    owner_notification_id = client.get("/notifications/me", headers=owner_headers).json()[0]["id"]

    other_headers = {"Authorization": f"Bearer {other_token}"}
    response = client.post(f"/notifications/me/{owner_notification_id}/read", headers=other_headers)
    assert response.status_code == 404


def _login_token(client, email):
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"]


def test_mark_all_notifications_read(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")

    account_id, token = _signup_and_login(client, "markallread@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    for e164 in ("+15551110000", "+15552220000"):
        send_notification(
            db_session, event_name="number.activated", account_id=account_id,
            recipient_email="markallread@example.com", context={"e164": e164},
        )

    assert client.get("/notifications/me/unread-count", headers=headers).json() == {"unread_count": 2}

    response = client.post("/notifications/me/read-all", headers=headers)
    assert response.status_code == 200
    assert response.json() == {"unread_count": 0}
    assert client.get("/notifications/me/unread-count", headers=headers).json() == {"unread_count": 0}


def test_push_subscribe_then_unsubscribe(client, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    account_id, token = _signup_and_login(client, "pushsubscribe@example.com")
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
    _, token = _signup_and_login(client, "pushupsert@example.com")
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
        recipient_email="pushfanout@example.com", context={"e164": "+15550001111"},
    )

    assert sent == [("https://push.example.com/fanout", "+15550001111 is active on Zoiko Local",
                      "Your number +15550001111 is now active. You can start making and receiving calls.")]

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
        recipient_email=signup_email, context={"e164": "+15550001111"},
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

    account_id, token = _signup_and_login(client, "pushlistregression@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    subscribe_response = client.post(
        "/notifications/push/subscribe", headers=headers,
        json={"endpoint": "https://push.example.com/listregression", "p256dh": "k", "auth": "a"},
    )
    assert subscribe_response.status_code == 200

    from app.notifications.service import send_notification
    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="pushlistregression@example.com", context={"e164": "+15550009000"},
    )

    response = client.get("/notifications/me", headers=headers)
    assert response.status_code == 200
    channels = {row["channel"] for row in response.json()}
    assert channels == {"email", "push"}
    push_row = next(row for row in response.json() if row["channel"] == "push")
    assert push_row["recipient_email"] is None
