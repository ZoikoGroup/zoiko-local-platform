import enum
import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class PhoneNumberStatus(str, enum.Enum):
    RESERVED = "reserved"
    PURCHASE_PENDING = "purchase_pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    e164: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="twilio")
    provider_sid: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    status: Mapped[PhoneNumberStatus] = mapped_column(
        Enum(PhoneNumberStatus, name="phone_number_status_enum"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Roadmap "Team and RBAC ... number assignment" — which team member this
    # number is handed to (e.g. a sales line given to one agent). NULL means
    # unassigned: any Owner/Admin on the account can still manage it, but no
    # plain Member can until an Owner/Admin assigns it to them.
    assigned_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Basic routing (Roadmap §2 "Voice: ... call forwarding ... business-hours
    # routing"). No forwarding_number = always go to voicemail. A forwarding
    # number with no business hours set = always forward.
    forwarding_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    business_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    business_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    business_hours_timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")

    # Roadmap §7 "AI Receptionist" — guarded caller-qualification flow when
    # no forwarding_number applies (or outside business hours). Off by
    # default: a number with neither forwarding nor this enabled just goes
    # straight to voicemail, same as before this feature existed.
    ai_receptionist_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
