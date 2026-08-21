from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class EventLog(Base):
    """Durable, replayable record of every event the consumer has read off
    the bus - distinct from audit_events (one row per domain transaction,
    keyed by actor/target) and from Kafka's own topic retention (bounded,
    not queryable). event_id is the envelope's own id, deduped on so
    replaying a partition (e.g. after a consumer restart) doesn't create
    duplicate rows."""

    __tablename__ = "event_log"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, unique=True, index=True)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    account_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventOutbox(Base):
    """Producer-side durability - the missing half of the classic outbox
    pattern. events.service.publish_event's own docstring is honest that
    it's best-effort ("a Kafka outage must never fail the business
    transaction") - correct, but as implemented that also means an event
    published at the exact moment Kafka is unreachable is silently
    dropped forever, with no durable record on our side that it should
    have gone out at all. EventLog above is the CONSUMER side's durable
    record (what was actually read off the bus); this is the PRODUCER
    side's durable record (what should be ON the bus, whether or not the
    publish attempt has succeeded yet).

    events.service.publish_event_durably() writes a row here via a plain
    db.add() - no commit - so the caller's own existing db.commit() covers
    both the business change and this row atomically: if that transaction
    rolls back, the event row never existed either, exactly the guarantee
    a fire-and-forget publish_event() call placed after commit can't give.
    A separate sweep, flush_pending_outbox_events(), then actually
    publishes to Kafka and retries until it succeeds - mirroring the
    Kafka CONSUMER side's own existing retry+DLQ precedent (see
    app.events.consumer), just for the producer side instead.

    Deliberately NOT a replacement for publish_event - wired into only
    the highest-value, money/entitlement-critical call sites, same
    scoping precedent already used for the original Kafka wiring itself
    ("representative call sites, not every domain event in the system" -
    see CLAUDE.md's 2026-08-04 exception note)."""

    __tablename__ = "event_outbox"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    topic: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    account_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
