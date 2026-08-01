import logging

from app.notifications.service import (
    notify_compliance_case_approved,
    notify_compliance_case_rejected,
    notify_number_activated,
    notify_number_suspended,
    notify_team_member_added,
)


def test_notify_number_activated_logs_when_no_smtp_configured(caplog):
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_number_activated("owner@example.com", "+15550001111")
    assert any("+15550001111" in record.message for record in caplog.records)


def test_notify_number_suspended_logs_when_no_smtp_configured(caplog):
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_number_suspended("owner@example.com", "+15550001111", reason="Non-payment")
    assert any("suspended" in record.message and "Non-payment" in record.message for record in caplog.records)


def test_notify_compliance_case_approved_logs_when_no_smtp_configured(caplog):
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_compliance_case_approved("owner@example.com", "US", "kyc_individual")
    assert any("approved" in record.message for record in caplog.records)


def test_notify_compliance_case_rejected_logs_when_no_smtp_configured(caplog):
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_compliance_case_rejected("owner@example.com", "US", "kyc_individual", reason="Blurry photo")
    assert any("Blurry photo" in record.message for record in caplog.records)


def test_notify_team_member_added_logs_when_no_smtp_configured(caplog):
    with caplog.at_level(logging.INFO, logger="zoiko.notifications"):
        notify_team_member_added("member@example.com", "Acme Inc", "admin")
    assert any("Acme Inc" in record.message for record in caplog.records)
