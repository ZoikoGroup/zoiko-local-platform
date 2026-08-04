import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class NotificationCategory(str, enum.Enum):
    TRANSACTIONAL = "transactional"
    SECURITY = "security"


class NotificationDeliveryStatus(str, enum.Enum):
    SENT = "sent"
    FAILED = "failed"


class NotificationChannel(str, enum.Enum):
    EMAIL = "email"
    SMS = "sms"


class NotificationTemplate(Base):
    """Scaled-down version of the "template registry" concept from the
    Email Communications System doc - one row per event type, holding the
    subject/body with {placeholder} variables, instead of every call site
    building its own hardcoded string."""

    __tablename__ = "notification_templates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    key: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    category: Mapped[NotificationCategory] = mapped_column(
        Enum(NotificationCategory, name="notification_category_enum"), nullable=False
    )
    subject_template: Mapped[str] = mapped_column(String(255), nullable=False)
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    # Nullable - most templates are email-only. Only the events safety-
    # critical enough to warrant a second channel (e.g. number suspended)
    # get a short SMS-appropriate body; the rest simply can't be sent by SMS.
    sms_body_template: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NotificationDelivery(Base):
    """Scaled-down version of the doc's "delivery ledger" - one row per
    send attempt, so a customer's own send history (the doc's
    "Communications History" trust surface) and delivery failures are both
    visible instead of disappearing into a log line."""

    __tablename__ = "notification_deliveries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    event_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, name="notification_channel_enum"),
        nullable=False,
        default=NotificationChannel.EMAIL,
    )
    # Exactly one of these is set, depending on channel - nullable rather
    # than two required columns since an SMS delivery has no email address
    # and vice versa.
    recipient_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    recipient_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        Enum(NotificationDeliveryStatus, name="notification_delivery_status_enum"), nullable=False
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # In-app notification center - the dashboard bell icon reads this ledger
    # directly rather than a separate table, since it's already the record
    # of every notification-worthy event. NULL means unread.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
