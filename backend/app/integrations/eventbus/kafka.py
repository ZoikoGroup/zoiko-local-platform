"""
Provider Gateway for the event bus (Kafka). Only file allowed to import
kafka-python directly. Falls back to logging when no bootstrap servers are
configured, matching this codebase's other providers - the interface stays
real without ever blocking local dev on a running broker.
"""

import json
import logging

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError

from app.core.config import settings

logger = logging.getLogger("zoiko.events")

_producer: KafkaProducer | None = None


class EventBusError(Exception):
    """Raised instead of letting a kafka-python-specific exception escape this module."""


def _get_producer() -> KafkaProducer:
    global _producer
    if _producer is None:
        _producer = KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            key_serializer=lambda k: k.encode("utf-8") if k else None,
            # Domain events are point-in-time facts, not a queue that must
            # never lose a message before this platform has any consumer
            # relying on durability guarantees - a handful of retries beats
            # either blocking the caller indefinitely or silently dropping.
            retries=3,
            request_timeout_ms=10_000,
        )
    return _producer


def health_check() -> dict:
    return {"configured": bool(settings.kafka_bootstrap_servers), "ok": True, "detail": None}


def publish(topic: str, key: str | None, payload: dict) -> None:
    if not settings.kafka_bootstrap_servers:
        logger.info("EVENT (no Kafka broker configured) topic=%s key=%s payload=%r", topic, key, payload)
        return

    try:
        future = _get_producer().send(topic, key=key, value=payload)
        future.get(timeout=10)
    except KafkaError as e:
        raise EventBusError(f"Kafka publish failed for topic {topic!r}: {e}") from e


def get_consumer(topics: list[str], group_id: str) -> KafkaConsumer:
    """Factory for a consumer - callers own its lifecycle (iterate, then
    .close()). Not used by the request/response path; for background
    workers and tests that need to prove an event was actually delivered."""
    return KafkaConsumer(
        *topics,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        auto_offset_reset="earliest",
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        consumer_timeout_ms=10_000,
    )
