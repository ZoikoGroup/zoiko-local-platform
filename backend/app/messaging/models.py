import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class MessagingChannel(str, enum.Enum):
    WHATSAPP = "whatsapp"
    # SMS by regulated market (architecture doc's separate Phase 3 item) is
    # a distinct workstream (carrier registration, opt-out law varies by
    # market) - this enum member is reserved so that work can reuse the
    # same Conversation/Message shape instead of a parallel table, but no
    # SMS sending path exists yet.
    SMS = "sms"


class MessageDirection(str, enum.Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class MessageStatus(str, enum.Enum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    RECEIVED = "received"


class Conversation(Base):
    """One row per (business number, external contact) pair - the email
    spec's MSG domain conversation model. `opted_out` blocks further
    outbound sends (email spec's "Recipient Opted Out" - MSG-007) until the
    contact re-subscribes.
    """

    __tablename__ = "messaging_conversations"
    __table_args__ = (UniqueConstraint("phone_number_id", "customer_number", "channel", name="uq_conversation_thread"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_number: Mapped[str] = mapped_column(String(20), nullable=False)
    channel: Mapped[MessagingChannel] = mapped_column(Enum(MessagingChannel, name="messaging_channel_enum"), nullable=False)
    opted_out: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    __tablename__ = "messaging_messages"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("messaging_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    direction: Mapped[MessageDirection] = mapped_column(Enum(MessageDirection, name="message_direction_enum"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    provider_message_sid: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    status: Mapped[MessageStatus] = mapped_column(Enum(MessageStatus, name="message_status_enum"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
