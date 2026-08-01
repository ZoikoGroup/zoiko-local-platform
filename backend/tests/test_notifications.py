import logging

from app.notifications.service import notify_number_activated


def test_notify_number_activated_logs_when_no_smtp_configured(caplog):
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_number_activated("owner@example.com", "+15550001111")
    assert any("+15550001111" in record.message for record in caplog.records)
