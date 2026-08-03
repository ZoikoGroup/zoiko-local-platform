from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class BlockedDestination(Base):
    """Platform-wide outbound-dialing blocklist (Architecture doc §5 "Fraud
    and Risk": "provider blacklists"; §13 Commercial: "blocked destinations").

    A rule as data, not a hardcoded if-statement (same "compliance as code"
    doctrine the ComplianceRule table follows) - staff-managed, checked
    against every outbound call regardless of account. `prefix` matches an
    E.164 number by startswith, so a rule can block a whole country code
    (e.g. "+234") or a narrower premium-rate range (e.g. "+1900").
    """

    __tablename__ = "blocked_destinations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    prefix: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
