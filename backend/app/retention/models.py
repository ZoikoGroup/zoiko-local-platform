import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, UniqueConstraint, func
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
