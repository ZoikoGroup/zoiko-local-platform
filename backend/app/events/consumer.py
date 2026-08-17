"""Background worker that reads the domain-event topics and writes a
durable, replayable copy of each event into Postgres (event_log) - the
first long-running worker process in this codebase. Run standalone:

    python -m app.events.consumer

Not wired into the FastAPI app itself; it owns its own DB sessions and its
own Kafka consumer group, independent of the request/response path.
"""

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.events.models import EventLog
from app.integrations.eventbus import kafka as eventbus
from app.integrations.eventbus.kafka import EventBusError

logger = logging.getLogger("zoiko.events")

TOPICS = [
    "zoiko.numbers", "zoiko.calls", "zoiko.notifications", "zoiko.video",
    "zoiko.voicemail", "zoiko.intelligence", "zoiko.usage", "zoiko.compliance",
    "zoiko.risk", "zoiko.billing", "zoiko.porting", "zoiko.messaging",
    "zoiko.queues", "zoiko.crm", "zoiko.apikeys", "zoiko.staff",
    "zoiko.consent", "zoiko.contacts", "zoiko.ops", "zoiko.retention", "zoiko.routing",
]
GROUP_ID = "event-log-writer"
DLQ_TOPIC = "zoiko.dlq"
MAX_ATTEMPTS = 3


def persist_event(db: Session, topic: str, envelope: dict) -> None:
    """Idempotent insert keyed on the envelope's event_id - replaying a
    partition (e.g. after a consumer restart before the last offset commit)
    must not create a duplicate row."""
    stmt = (
        pg_insert(EventLog)
        .values(
            event_id=envelope["event_id"],
            topic=topic,
            event_type=envelope["event_type"],
            account_id=envelope.get("account_id"),
            payload=envelope,
        )
        .on_conflict_do_nothing(index_elements=["event_id"])
    )
    db.execute(stmt)
    db.commit()


def send_to_dlq(topic: str, envelope: dict, error: str) -> None:
    try:
        eventbus.publish(
            topic=DLQ_TOPIC, key=envelope.get("account_id"),
            payload={**envelope, "original_topic": topic, "error": error},
        )
    except EventBusError:
        logger.error("Failed to publish event %s to DLQ after exhausting retries", envelope.get("event_id"))


def handle_message(topic: str, envelope: dict) -> None:
    """Never lets one bad message block the consumer group - a handful of
    retries against fresh sessions, then off to the DLQ topic and move on."""
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        db = SessionLocal()
        try:
            persist_event(db, topic, envelope)
            return
        except Exception as e:
            db.rollback()
            last_error = e
            logger.warning(
                "Failed to persist event %s (attempt %d/%d)",
                envelope.get("event_id"), attempt, MAX_ATTEMPTS, exc_info=True,
            )
        finally:
            db.close()
    send_to_dlq(topic, envelope, str(last_error))


def run() -> None:
    logger.info("event-log consumer starting - topics=%s group=%s", TOPICS, GROUP_ID)
    consumer = eventbus.get_consumer(TOPICS, GROUP_ID)
    try:
        while True:
            for message in consumer:
                handle_message(message.topic, message.value)
    except KeyboardInterrupt:
        logger.info("event-log consumer shutting down")
    finally:
        consumer.close()


if __name__ == "__main__":
    run()
