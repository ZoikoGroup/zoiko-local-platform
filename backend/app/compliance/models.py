import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class ConsentType(str, enum.Enum):
    AI_PROCESSING = "ai_processing"


class ConsentRecord(Base):
    """One row per (account_id, consent_type). Per the roadmap doc's
    Automatic No-Go trigger: 'AI processes call, voicemail or receptionist
    content without valid consent and legal basis' blocks launch — this is
    the gate that prevents that.
    """

    __tablename__ = "consent_records"
    __table_args__ = (UniqueConstraint("account_id", "consent_type", name="uq_consent_account_type"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_type: Mapped[ConsentType] = mapped_column(Enum(ConsentType, name="consent_type_enum"), nullable=False)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
