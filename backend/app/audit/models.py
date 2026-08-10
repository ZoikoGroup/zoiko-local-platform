from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class AuditEvent(Base):
    """Append-only evidence of state change or admin action.

    Shape matches the Backend Architecture doc's data model exactly:
    audit_id, actor, action, target, before_hash, after_hash,
    timestamp, reason, correlation_id.

    Note: kept this schema (over an alternative actor_id/target_type/
    target_id/event_metadata design from a parallel branch) since more
    modules already depend on it - see log_event()'s compatibility
    parameters in service.py for how the other calling convention maps
    onto this same table.
    """

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    actor: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Resolved once at write time in log_event() (see _resolve_account_id) -
    # nullable because some actors (staff, "system", cross-account platform
    # actions) genuinely have no owning customer account. Not a foreign key:
    # events must survive an account's deletion for compliance/evidentiary
    # reasons, same posture as before_hash/after_hash being one-way hashes
    # rather than live references.
    account_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    before_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    after_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
