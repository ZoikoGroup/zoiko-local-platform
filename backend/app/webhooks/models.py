import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class WebhookDeliveryStatus(str, enum.Enum):
    DELIVERED = "delivered"
    FAILED = "failed"


class WebhookEndpoint(Base):
    """Architecture doc's Phase 2 "start with export and webhook-ready
    events internally" - a customer-registered URL that receives a POST
    for every notification-worthy event on their account (see
    app.notifications.service.send_notification, the single dispatch
    point both channels share)."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Generated once at creation (see service.create_endpoint) and never
    # re-exposed after - only used server-side to HMAC-sign delivery
    # bodies, so the customer's receiving server can verify a delivery
    # actually came from Zoiko Local.
    secret: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    """One row per attempted POST - the customer-visible debugging surface
    ("why didn't my endpoint receive event X") and the retry-later
    candidate list, same ledger role NotificationDelivery plays for email."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    endpoint_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus, name="webhook_delivery_status_enum"), nullable=False
    )
    response_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
