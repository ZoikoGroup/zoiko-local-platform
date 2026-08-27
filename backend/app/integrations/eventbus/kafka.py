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
            # Real gap fix, confirmed live: this module's whole docstring/
            # design promise is "fire-and-forget... returns without waiting
            # for the broker's ack" (see publish()'s own docstring on the
            # prior future.get(timeout=10) bug it already fixed) - but two
            # separate, unaddressed defaults undermined that same promise:
            # api_version left at its default (None) makes kafka-python do
            # a real, blocking broker-version-detection handshake the FIRST
            # time this producer is constructed in each worker process
            # (once per WEB_CONCURRENCY worker, whichever request happens
            # to trigger the first domain-event publish pays for it); and
            # max_block_ms's default (60_000) governs a SEPARATE, earlier
            # blocking point than the ack-wait fix already addressed -
            # producer.send() itself synchronously waits up to this long
            # for topic metadata before ever handing the message off to the
            # background sender thread, on the first publish to any given
            # topic in this producer's lifetime, or anytime the broker is
            # slow/unreachable and cached metadata goes stale. The
            # resulting KafkaTimeoutError is already caught correctly
            # (publish()'s except KafkaError below, then events.service.
            # publish_event's own try/except) and never fails the caller's
            # request - but correctness isn't the gap here, latency is: a
            # request that happens to hit this could silently block for up
            # to a full minute before that safety net even engages.
            # api_version pinned to match docker-compose's actual broker
            # (apache/kafka:3.8.0) skips the auto-detection handshake
            # entirely; max_block_ms lowered so a real metadata-fetch
            # failure fails fast instead of near-hanging the request.
            api_version=(3, 8, 0),
            max_block_ms=2_000,
        )
    return _producer


def health_check() -> dict:
    return {"configured": bool(settings.kafka_bootstrap_servers), "ok": True, "detail": None}


def publish(topic: str, key: str | None, payload: dict) -> None:
    """Fire-and-forget: hands the message to kafka-python's background
    sender thread and returns without waiting for the broker's ack.
    Confirmed live (found via a real backend test run): the previous
    `future.get(timeout=10)` blocked the calling request/transaction for
    up to 10 real seconds on every single publish - a degraded-but-not-
    down broker wouldn't just risk failing the transaction (already
    handled below), it could make the transaction itself slow enough to
    trip an unrelated timeout elsewhere (e.g. the request's own DB
    connection checkout), which contradicts this module's whole
    "best-effort, never impacts the underlying business transaction"
    design intent - "never fails it" isn't the same guarantee as "never
    slows it down by seconds." Delivery failures are still logged (via
    the future's errback), just asynchronously - by the time this
    returns, only failures that happen before the message is even
    handed to the producer (e.g. the broker being unreachable at send()
    call time, which can still raise synchronously) surface as
    EventBusError to the caller. This means events/service.py's
    _publish_failures counter under-counts true async delivery failures
    (it only sees the synchronous ones) - a known, accepted gap rather
    than threading a callback back through this Provider Gateway's
    signature, which would ripple into every test double that mocks
    this function (see test_events.py's own monkeypatched fake, which
    only accepts topic/key/payload)."""
    if not settings.kafka_bootstrap_servers:
        logger.info("EVENT (no Kafka broker configured) topic=%s key=%s payload=%r", topic, key, payload)
        return

    def _log_delivery_failure(exc: Exception) -> None:
        logger.warning("Kafka publish failed for topic %r (async): %s", topic, exc)

    try:
        future = _get_producer().send(topic, key=key, value=payload)
        future.add_errback(_log_delivery_failure)
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
