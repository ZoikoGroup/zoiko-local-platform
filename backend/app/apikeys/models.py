from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class ApiKey(Base):
    """Public API + developer portal (Architecture doc Phase 2, "public
    contracts... wait until domain behavior is stable" - the public
    surface is deliberately a small, curated read-only subset, not a
    mirror of every internal route). Only key_hash is stored - the raw
    key (see service.generate_key) is shown exactly once, at creation,
    same posture as WebhookEndpoint.secret."""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    # First 12 chars of the raw key (e.g. "zlk_live_ab3f") - shown in the
    # UI so a customer can tell keys apart without ever re-seeing the
    # full secret.
    key_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
