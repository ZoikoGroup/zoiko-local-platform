import enum
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class NotificationCategory(str, enum.Enum):
    TRANSACTIONAL = "transactional"
    SECURITY = "security"


class NotificationPriority(str, enum.Enum):
    # CRITICAL bypasses both quiet hours and the transactional-suppression
    # preference - reserved for events where NOT telling the customer is
    # itself the harmful outcome (losing access to a number, account
    # security). Everything else is STANDARD.
    CRITICAL = "critical"
    STANDARD = "standard"


class NotificationDeliveryStatus(str, enum.Enum):
    SENT = "sent"
    FAILED = "failed"
    # Not sent on purpose - either the customer opted out of this category
    # (see NotificationPreference.transactional_enabled) or it landed inside
    # their configured quiet hours. Kept as its own ledger row (not silently
    # dropped) so "why didn't I get this" has a real answer.
    SUPPRESSED = "suppressed"


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
    priority: Mapped[NotificationPriority] = mapped_column(
        Enum(NotificationPriority, name="notification_priority_enum"),
        nullable=False,
        default=NotificationPriority.STANDARD,
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


class NotificationPreference(Base):
    """One row per account (not per user) - notifications go to the account
    owner's email/phone today, so preferences are scoped the same way.
    Created lazily on first read/write (see service.get_or_create_preference)
    rather than at account-creation time, so every account doesn't need a
    migration-time backfill row."""

    __tablename__ = "notification_preferences"

    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True
    )
    # SECURITY-category and CRITICAL-priority templates ignore this - opting
    # out of transactional noise must never silently opt someone out of
    # "your account access changed" type events.
    transactional_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
