from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class UsageEvent(Base):
    """Rating input for a future billing integration (Architecture doc §7
    data model: "Usage Event... rating input for ZoikoNex"; Roadmap §2 lists
    "usage metering" as Phase 1 Voice scope in its own right, not contingent
    on ZoikoNex existing). Zoiko Local owns real-time usage capture; nothing
    downstream (ZoikoNex) exists yet to consume it, but the capture itself
    doesn't need to wait on that integration.

    idempotency_key prevents double-counting when a provider webhook (e.g.
    Twilio's status-callback) fires more than once for the same event.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    country_band: Mapped[str | None] = mapped_column(String(2), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
