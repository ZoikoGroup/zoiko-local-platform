import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.integrations.embeddings.cohere import EMBEDDING_DIMENSIONS
from app.media.models import ReceptionistUrgency


def new_uuid() -> str:
    return str(uuid.uuid4())


class SummarySourceType(str, enum.Enum):
    VOICEMAIL = "voicemail"
    CALL = "call"
    VIDEO = "video"


class AIJobStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AIJob(Base):
    """Retry lineage and failure observability for AI summarization jobs -
    the gap a previous audit flagged: before this, a failed transcription
    or LLM call left NO row anywhere (ConversationSummary is only ever
    created on success), so "why did this recording never get a summary"
    was unanswerable without grepping raw logs, and a retried job looked
    identical to a first attempt. One row per (source_type, source_id) -
    a retry after failure updates the SAME row (attempt_count increments)
    rather than creating a new one, so the row's own history tells the
    whole story of one job across every attempt. Deliberately doesn't
    replace ConversationSummary (the actual result) - see
    app.intelligence.service._run_ai_job for how the two connect."""

    __tablename__ = "ai_jobs"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_ai_job_source"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[SummarySourceType] = mapped_column(Enum(SummarySourceType, name="summary_source_type_enum"), nullable=False)
    source_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    status: Mapped[AIJobStatus] = mapped_column(Enum(AIJobStatus, name="ai_job_status_enum"), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set only once the job actually succeeds - the real result lives in
    # ConversationSummary, this is just a pointer to it.
    conversation_summary_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("conversation_summaries.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ConversationSummary(Base):
    __tablename__ = "conversation_summaries"
    __table_args__ = (
        Index(
            "conversation_summaries_embedding_hnsw_idx", "embedding",
            postgresql_using="hnsw", postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

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

    # AI governance: outputs must be "human-editable" (Architecture doc
    # §2.3), not just labelled non-authoritative. original_summary is only
    # ever populated on the FIRST edit (preserves what the model actually
    # said, for evidence/traceability), never touched again on subsequent
    # edits - `summary` above always holds the current, possibly
    # human-corrected text that's actually shown.
    original_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    edited_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Real semantic search (Architecture doc: "pgvector acceptable for MVP
    # semantic search"). Nullable - populated at creation time going
    # forward; a Cohere failure degrades to no embedding (search just won't
    # surface that record) rather than failing the whole summarize call.
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=True)
