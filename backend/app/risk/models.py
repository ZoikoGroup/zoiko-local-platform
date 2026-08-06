import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
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


class RiskSignalType(str, enum.Enum):
    VELOCITY_EXCEEDED = "velocity_exceeded"
    BLOCKED_DESTINATION_ATTEMPT = "blocked_destination_attempt"


class RiskSignal(Base):
    """One occurrence of a fraud/abuse rule actually firing against an
    account (Roadmap doc §13 Risk Register: "anomalous usage", "account risk
    scoring"). `assert_destination_allowed`/`assert_outbound_velocity_ok`
    already blocked the call in the moment by raising - this table is the
    evidence trail of *how often* that's happened per account, which is what
    a risk score and an auto-suspend decision need to work from. Without it,
    a repeatedly-abusive account looks identical to one that tripped a rule
    once, since the block itself leaves no queryable row anywhere but the
    audit log's free-text fields.
    """

    __tablename__ = "risk_signals"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_type: Mapped[RiskSignalType] = mapped_column(Enum(RiskSignalType), nullable=False)
    detail: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
