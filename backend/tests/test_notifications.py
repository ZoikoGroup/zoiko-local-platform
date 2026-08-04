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
    assert body[0]["channel"] == "email"
    assert body[0]["read_at"] is None


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
        recipient_email="notifyread1@example.com", context={"e164": "+15550001234"},
    )

    response = client.post(
        f"/notifications/{delivery.id}/read", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json()["read_at"] is not None

    listed = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert listed[0]["read_at"] is not None


def test_mark_notification_read_rejects_other_account(client, db_session, monkeypatch):
    from app.notifications.service import send_notification

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "")
    _, owner_account_id = _signup_and_login(client, "notifyread2owner@example.com")
    delivery = send_notification(
        db_session, event_name="number.activated", account_id=owner_account_id,
        recipient_email="notifyread2owner@example.com", context={"e164": "+15550005678"},
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
        recipient_email="notifyreadall@example.com", context={"e164": "+15550001111"},
    )
    send_notification(
        db_session, event_name="number.activated", account_id=account_id,
        recipient_email="notifyreadall@example.com", context={"e164": "+15550002222"},
    )

    response = client.post("/notifications/read-all", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json()["marked_read"] == 2

    listed = client.get("/notifications/me", headers={"Authorization": f"Bearer {token}"}).json()
    assert all(n["read_at"] is not None for n in listed)


def test_mark_notification_read_requires_auth(client):
    response = client.post("/notifications/some-id/read")
    assert response.status_code == 401
