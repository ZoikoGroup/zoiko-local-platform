import enum
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.media.models import ReceptionistUrgency


def new_uuid() -> str:
    return str(uuid.uuid4())


class SummarySourceType(str, enum.Enum):
    VOICEMAIL = "voicemail"
    CALL = "call"


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[SummarySourceType] = mapped_column(
        Enum(SummarySourceType, name="summary_source_type_enum"), nullable=False
    )
    # Polymorphic reference to either voicemails.id or call_records.id — no FK
    # constraint since it can point to either table.
    source_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    # Structured AI intelligence (Architecture doc §2.3 Phase 1 AI: "language
    # detection... AI-generated action extraction"; Roadmap §2: "summary,
    # language detection, suggested follow-up") - nullable because they're
    # populated from the LLM's JSON output, which degrades gracefully to
    # null/empty rather than failing the whole summary if a field is missing.
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    urgency: Mapped[ReceptionistUrgency | None] = mapped_column(
        Enum(ReceptionistUrgency, name="summary_urgency_enum"), nullable=True
    )
    action_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    suggested_follow_up: Mapped[str | None] = mapped_column(Text, nullable=True)
    # evidence per the architecture doc's AI governance requirement: outputs
    # must be explainable/traceable to the exact model version that produced them
    model_version: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
