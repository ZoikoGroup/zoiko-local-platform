import logging
import uuid

from app.events.service import publish_event, publish_number_reserved
from app.integrations.eventbus.kafka import EventBusError


def test_publish_event_logs_when_no_broker_configured(monkeypatch, caplog):
    monkeypatch.setattr("app.core.config.settings.kafka_bootstrap_servers", "")
    with caplog.at_level(logging.INFO, logger="zoiko.events"):
        publish_event("zoiko.test", "test.event", "acct-1", {"foo": "bar"})
    assert any("test.event" in record.message for record in caplog.records)


def test_publish_event_calls_eventbus_publish_with_envelope(monkeypatch):
    captured = {}

    def _fake_publish(topic, key, payload):
        captured["topic"] = topic
        captured["key"] = key
        captured["payload"] = payload

    monkeypatch.setattr("app.events.service.eventbus.publish", _fake_publish)

    publish_number_reserved("acct-123", number_id="num-1", e164="+15550001111", country="US")

    assert captured["topic"] == "zoiko.numbers"
    assert captured["key"] == "acct-123"
    assert captured["payload"]["event_type"] == "number.reserved"
    assert captured["payload"]["account_id"] == "acct-123"
    assert captured["payload"]["data"] == {"number_id": "num-1", "e164": "+15550001111", "country": "US"}
    assert "occurred_at" in captured["payload"]


def test_publish_event_swallows_eventbus_errors(monkeypatch, caplog):
    def _raise(topic, key, payload):
        raise EventBusError("broker unreachable")

    monkeypatch.setattr("app.events.service.eventbus.publish", _raise)

    with caplog.at_level(logging.WARNING, logger="zoiko.events"):
        publish_event("zoiko.test", "test.event", "acct-1", {"foo": "bar"})  # must not raise

    assert any("Failed to publish event" in record.message for record in caplog.records)


def test_publish_event_real_kafka_roundtrip():
    """Publishes to, and consumes back from, the real docker-compose Kafka
    broker - not mocked - matching this codebase's practice of testing
    against real providers rather than stubs wherever one is reachable."""
    from app.core.config import settings
    from app.integrations.eventbus import kafka as eventbus

    if not settings.kafka_bootstrap_servers:
        import pytest
        pytest.skip("KAFKA_BOOTSTRAP_SERVERS not configured - start it with `docker compose up -d kafka`")

    marker = str(uuid.uuid4())
    publish_event("zoiko.test", "test.roundtrip", "acct-roundtrip", {"marker": marker})

    consumer = eventbus.get_consumer(["zoiko.test"], group_id=f"test-{marker}")
    try:
        found = None
        for message in consumer:
            if message.value.get("data", {}).get("marker") == marker:
                found = message.value
                break
    finally:
        consumer.close()

    assert found is not None, "published event was never consumed back from the real broker"
    assert found["event_type"] == "test.roundtrip"
    assert found["account_id"] == "acct-roundtrip"
