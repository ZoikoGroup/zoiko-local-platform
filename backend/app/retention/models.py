import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class ArtifactType(str, enum.Enum):
    VOICEMAIL = "voicemail"
    CALL_RECORDING = "call_recording"
    VIDEO_RECORDING = "video_recording"


class RetentionPolicy(Base):
    """Roadmap doc: 'Configurable retention by artifact type' — one row per
    (account_id, artifact_type). account_id is nullable to represent the
    platform-wide default (seeded once via migration); an account only gets
    its own row here if it has overridden that default.
    """

    __tablename__ = "retention_policies"
    __table_args__ = (UniqueConstraint("account_id", "artifact_type", name="uq_retention_account_artifact"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    artifact_type: Mapped[ArtifactType] = mapped_column(
        Enum(ArtifactType, name="retention_artifact_type_enum"), nullable=False
    )
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ErasureRequestStatus(str, enum.Enum):
    """Matches the DB enum erasure_request_status_enum's member names
    exactly (migration c3b0f40f4bc1) - these are the enum VALUES stored in
    Postgres, not lowercased."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class ErasureRequest(Base):
    """A customer's request that their account's data be erased ("right to
    be forgotten" / GDPR-style deletion request). requested_by is NOT a
    foreign key on purpose - it can reference either a numbering.identity.
    models.User.id (a customer requesting their own account's erasure) or a
    app.staff.models.PlatformStaff.id (staff opening one on a customer's
    behalf), and there's no single table both would cleanly FK to.

    Marking a request COMPLETED records that a human resolved it through
    whatever real deletion process they used OUTSIDE this system - see
    app.retention.service.resolve_erasure_request's docstring for the exact
    scope boundary (this table does not itself delete any data).
    """

    __tablename__ = "erasure_requests"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    status: Mapped[ErasureRequestStatus] = mapped_column(
        Enum(ErasureRequestStatus, name="erasure_request_status_enum"),
        nullable=False, default=ErasureRequestStatus.PENDING,
    )
    notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
