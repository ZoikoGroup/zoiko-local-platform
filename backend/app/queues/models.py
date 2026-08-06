import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class CallQueue(Base):
    """Architecture doc's "contact-center-lite" (Phase 3) - a named FIFO
    hold queue, distinct from the ring-group/FORWARD call-flow node (which
    rings every destination at once with no hold, no wait tracking, and no
    agent presence). Backed by a real Twilio Queue, auto-created the first
    time a call is <Enqueue>'d into it - see queues.service.twilio_queue_name.
    """

    __tablename__ = "call_queues"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # How long a caller waits before falling through to the call flow's
    # queue node's overflow_node_id (default: voicemail) - checked lazily on
    # each waitUrl poll, not by a background job.
    max_wait_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120)

    # How long an agent stays ineligible for the NEXT pull after finishing a
    # queue call - "wrap-up time" in the architecture doc's contact-center
    # vocabulary. Snapshotted onto AgentPresence.wrap_up_until at the moment
    # a call ends, not re-read from here later, so changing this doesn't
    # retroactively affect an agent already mid-wrap-up.
    wrap_up_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QueueMember(Base):
    __tablename__ = "queue_members"
    __table_args__ = (UniqueConstraint("queue_id", "user_id", name="uq_queue_member"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    queue_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("call_queues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentPresenceStatus(str, enum.Enum):
    AVAILABLE = "available"
    WRAP_UP = "wrap_up"
    OFFLINE = "offline"


class AgentPresence(Base):
    """One row per user, account-independent (a user belongs to exactly one
    account already). Deliberately its own table rather than columns on
    identity.User - presence is a contact-center concept, not an identity
    one, and keeping it separate matches the architecture doc's plane
    separation instead of leaking media/routing concerns into Identity.
    """

    __tablename__ = "agent_presence"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    status: Mapped[AgentPresenceStatus] = mapped_column(
        Enum(AgentPresenceStatus, name="agent_presence_status_enum"),
        nullable=False,
        default=AgentPresenceStatus.OFFLINE,
    )
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Only meaningful while status == WRAP_UP - see CallQueue.wrap_up_seconds.
    wrap_up_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class QueueCallOutcome(str, enum.Enum):
    WAITING = "waiting"
    ANSWERED = "answered"
    ABANDONED = "abandoned"
    OVERFLOWED = "overflowed"


class QueueCallLog(Base):
    """One row per call that ever entered a queue - the SLA/audit trail the
    architecture doc's "Queue SLA Alert" and "audit" requirements need,
    kept in our own DB rather than re-derived from Twilio's Queue REST
    resource on every read (that resource is addressed by SID, requires an
    extra round-trip, and doesn't retain history once a call leaves).
    """

    __tablename__ = "queue_call_logs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    queue_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("call_queues.id", ondelete="CASCADE"), nullable=False, index=True
    )
    call_sid: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    caller_number: Mapped[str] = mapped_column(String(20), nullable=False)
    # The number the caller originally dialed - used as the agent-facing
    # call's caller ID when they're pulled off this queue (see
    # queues.service.pull_next_caller).
    phone_number_e164: Mapped[str | None] = mapped_column(String(20), nullable=True)

    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    agent_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[QueueCallOutcome] = mapped_column(
        Enum(QueueCallOutcome, name="queue_call_outcome_enum"), nullable=False, default=QueueCallOutcome.WAITING
    )
