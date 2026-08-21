import logging
import uuid

from app.events.consumer import handle_message, persist_event
from app.events.models import EventLog, EventOutbox
from app.events.service import (
    flush_pending_outbox_events,
    publish_event,
    publish_event_durably,
    publish_number_reserved,
    publish_video_room_created,
)
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


def test_publish_video_room_created_has_event_id_and_correct_shape(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "app.events.service.eventbus.publish",
        lambda topic, key, payload: captured.update(topic=topic, key=key, payload=payload),
    )

    publish_video_room_created("acct-video", room_name="zl-abc123")

    assert captured["topic"] == "zoiko.video"
    assert captured["payload"]["event_type"] == "video.room.created"
    assert captured["payload"]["data"] == {"room_name": "zl-abc123"}
    assert uuid.UUID(captured["payload"]["event_id"])  # must be a real uuid, not just present


def test_persist_event_is_idempotent_on_duplicate_event_id(db_session):
    envelope = {
        "event_id": str(uuid.uuid4()),
        "event_type": "number.reserved",
        "occurred_at": "2026-08-05T00:00:00+00:00",
        "account_id": str(uuid.uuid4()),
        "data": {"number_id": "num-1"},
    }

    persist_event(db_session, "zoiko.numbers", envelope)
    persist_event(db_session, "zoiko.numbers", envelope)  # replay of the same message

    rows = db_session.query(EventLog).filter(EventLog.event_id == envelope["event_id"]).all()
    assert len(rows) == 1
    assert rows[0].topic == "zoiko.numbers"
    assert rows[0].event_type == "number.reserved"


def test_handle_message_sends_to_dlq_after_repeated_persist_failures(monkeypatch):
    def _always_fail(db, topic, envelope):
        raise RuntimeError("simulated persistence failure")

    monkeypatch.setattr("app.events.consumer.persist_event", _always_fail)

    dlq = {}
    monkeypatch.setattr(
        "app.events.consumer.eventbus.publish",
        lambda topic, key, payload: dlq.update(topic=topic, key=key, payload=payload),
    )

    envelope = {
        "event_id": str(uuid.uuid4()), "event_type": "call.started", "account_id": "acct-dlq", "data": {},
    }
    handle_message("zoiko.calls", envelope)

    assert dlq["topic"] == "zoiko.dlq"
    assert dlq["payload"]["original_topic"] == "zoiko.calls"
    assert dlq["payload"]["event_id"] == envelope["event_id"]
    assert "simulated persistence failure" in dlq["payload"]["error"]


# --- Outbox pattern (producer-side durability) ---


def test_publish_event_durably_writes_a_row_without_committing(db_session):
    """publish_event_durably must NOT commit itself - that's the whole
    point (see EventOutbox's docstring): the caller's own commit is what
    makes the event row atomic with the business change it records."""
    account_id = str(uuid.uuid4())
    event_id = publish_event_durably(
        db_session, "zoiko.billing", "subscription.plan_changed", account_id,
        {"subscription_id": "sub-1", "previous_plan": "starter", "new_plan": "pro"},
    )
    assert event_id is not None

    # Visible within the same uncommitted session (flush, not commit).
    row = db_session.query(EventOutbox).filter(EventOutbox.payload["event_id"].astext == event_id).first()
    assert row is not None
    assert row.topic == "zoiko.billing"
    assert row.event_type == "subscription.plan_changed"
    assert row.account_id == account_id
    assert row.published_at is None
    assert row.payload["data"] == {"subscription_id": "sub-1", "previous_plan": "starter", "new_plan": "pro"}


def test_flush_pending_outbox_events_publishes_and_marks_rows(db_session, monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.events.service.eventbus.publish",
        lambda topic, key, payload: published.append((topic, key, payload)),
    )

    account_id = str(uuid.uuid4())
    publish_event_durably(db_session, "zoiko.numbers", "number.activated", account_id, {"number_id": "num-2"})
    db_session.commit()

    # Real dev DB, not a fresh test DB - other pending rows can genuinely
    # exist from other activity, so this asserts on THIS test's own row
    # (scoped by account_id), not the flush's aggregate counts.
    flush_pending_outbox_events(db_session)
    assert any(topic == "zoiko.numbers" for topic, _key, _payload in published)

    row = db_session.query(EventOutbox).filter(EventOutbox.account_id == account_id).first()
    assert row.published_at is not None


def test_flush_pending_outbox_events_records_failure_without_marking_published(db_session, monkeypatch):
    def _raise(topic, key, payload):
        raise EventBusError("broker unreachable")

    monkeypatch.setattr("app.events.service.eventbus.publish", _raise)

    account_id = str(uuid.uuid4())
    publish_event_durably(db_session, "zoiko.numbers", "number.activated", account_id, {"number_id": "num-3"})
    db_session.commit()

    flush_pending_outbox_events(db_session)

    row = db_session.query(EventOutbox).filter(EventOutbox.account_id == account_id).first()
    assert row.published_at is None
    assert row.attempt_count == 1
    assert "broker unreachable" in row.last_error


def test_flush_pending_outbox_events_does_not_republish_an_already_published_row(db_session, monkeypatch):
    published = []
    monkeypatch.setattr(
        "app.events.service.eventbus.publish",
        lambda topic, key, payload: published.append(topic),
    )

    account_id = str(uuid.uuid4())
    publish_event_durably(db_session, "zoiko.numbers", "number.activated", account_id, {"number_id": "num-4"})
    db_session.commit()

    flush_pending_outbox_events(db_session)
    first_publish_count = len(published)
    flush_pending_outbox_events(db_session)

    assert len(published) == first_publish_count  # not published a second time


def test_change_plan_writes_a_durable_outbox_event(client, db_session):
    """Integration test for the one representative call site this
    session wired: billing_service.change_plan now writes an EventOutbox
    row in the same transaction as the plan change, instead of the old
    fire-and-forget publish_subscription_plan_changed after commit."""
    from app.billing import service as billing_service
    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Outbox Test Co", account_type=AccountType.BUSINESS)
    db_session.add(account)
    db_session.flush()

    billing_service.change_plan(db_session, account.id, "starter", actor="test-actor")

    row = (
        db_session.query(EventOutbox)
        .filter(EventOutbox.event_type == "subscription.plan_changed", EventOutbox.account_id == account.id)
        .first()
    )
    assert row is not None
    assert row.payload["data"]["new_plan"] == "starter"
    assert row.payload["data"]["previous_plan"] == "free_trial"
