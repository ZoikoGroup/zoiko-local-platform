from datetime import datetime

from sqlalchemy import DateTime, String, func
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
