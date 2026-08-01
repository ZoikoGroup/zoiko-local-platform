import enum
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class ComplianceCaseStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ComplianceRule(Base):
    """Rules stored as data, never hardcoded if-statements (per the docs'
    explicit rule). One row per (country, requirement_type). This is the
    part the docs specify the *need* for but not the exact shape -
    modeled the way mature telecom platforms structure it: a lookup
    table checked at the point of action, not a workflow engine.
    """

    __tablename__ = "compliance_rules"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    requirement_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    required_documents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ComplianceCase(Base):
    """Country-specific evidence and approvals for number/KYC verification.

    Field shape matches the Backend Architecture doc's data model exactly:
    case_id, account_id, number_id, jurisdiction, requirement_type,
    status, documents, expires_at.

    Note: this is KYC/identity verification specifically. AI-processing
    consent is a separate concern - see app/consent/ (merged from a
    parallel branch, kept in its own module rather than sharing this
    file, since the two are unrelated despite both being "compliance").

    number_id is a plain string (not yet a foreign key) since the
    numbering/numbers table's FK relationship hasn't been wired up here.
    """

    __tablename__ = "compliance_cases"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    jurisdiction: Mapped[str] = mapped_column(String(2), nullable=False)
    requirement_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ComplianceCaseStatus] = mapped_column(
        Enum(ComplianceCaseStatus, name="compliance_case_status_enum"),
        nullable=False,
        default=ComplianceCaseStatus.PENDING,
    )
    documents: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
