import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid

# Sentinel jurisdiction for consent that isn't tied to any specific country
# (e.g. video calls, which have no inherent country the way a phone number
# does). Architecture doc's Consent Record model calls for a `jurisdiction`
# field on every record - this is the "applies everywhere" value for it,
# not an exemption from having one.
GLOBAL_JURISDICTION = "GLOBAL"


class ConsentType(str, enum.Enum):
    AI_PROCESSING = "ai_processing"


class ConsentRecord(Base):
    """One row per (account_id, consent_type, jurisdiction) - the
    jurisdiction column is what makes this the doc's "jurisdiction-aware"
    consent, since recording/AI-processing consent requirements genuinely
    differ by country (e.g. one-party vs two-party recording-consent laws).
    A GLOBAL_JURISDICTION grant is a superset that covers every jurisdiction
    (see consent/service.py's has_active_consent) - that's what the existing
    single "grant consent" button in the product still grants, so it keeps
    working exactly as before; a narrower per-country grant is an available
    but not yet UI-exposed capability.

    Per the roadmap doc's Automatic No-Go trigger: 'AI processes call,
    voicemail or receptionist content without valid consent and legal
    basis' blocks launch - this is the gate that prevents that.

    Kept as its own module (moved out of app/compliance/ during a branch
    merge) since it's a distinct concern from KYC/identity verification,
    which is what app/compliance/ is for - despite the overlapping name,
    these were never the same feature.
    """

    __tablename__ = "consent_records"
    __table_args__ = (
        UniqueConstraint("account_id", "consent_type", "jurisdiction", name="uq_consent_account_type_jurisdiction"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    consent_type: Mapped[ConsentType] = mapped_column(Enum(ConsentType, name="consent_type_enum"), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(10), nullable=False, default=GLOBAL_JURISDICTION)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
