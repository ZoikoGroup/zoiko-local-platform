import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class SubscriptionStatus(str, enum.Enum):
    TRIALING = "trialing"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"


class Plan(Base):
    """Architecture doc §5: "Subscription and Entitlement... Plans, limits,
    feature gates, number allowances, minute/video/AI quotas, grace
    periods" - a Zoiko-owned service, explicitly separate from the
    "ZoikoNex Billing Adapter" row in the same table. Stored as data (not
    hardcoded limits in Python), same discipline as this project's
    compliance rules, so plan limits can be tuned without a deploy.

    No price fields - no payment processing exists here at all (that's
    ZoikoNex's job once the connection is built). This purely gates
    feature/resource usage.
    """

    __tablename__ = "plans"

    plan_code: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    max_numbers: Mapped[int] = mapped_column(Integer, nullable=False)
    max_team_seats: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_voice_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_video_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_ai_summaries: Mapped[int] = mapped_column(Integer, nullable=False)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Subscription(Base):
    """Architecture doc §7 data model: "Commercial entitlement container
    synced with ZoikoNex" - subscription_id, plan_code, status, period,
    entitlements_snapshot, zoikonex_ref. entitlements_snapshot is
    deliberately NOT stored separately here - Plan IS the entitlements
    snapshot for Phase 1 (a live FK, not a point-in-time copy), since there's
    no billing cycle yet where a snapshot would diverge from the current
    plan definition.

    zoikonex_ref is populated by the MOCK adapter (app.integrations.billing
    .zoikonex) since there is no real ZoikoNex API yet — see that module's
    docstring for why this exists at all despite the event contract never
    being locked. Swapping the mock for a real client later needs no schema
    change here.
    """

    __tablename__ = "subscriptions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    plan_code: Mapped[str] = mapped_column(String(50), ForeignKey("plans.plan_code"), nullable=False)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status_enum"), nullable=False, default=SubscriptionStatus.TRIALING
    )
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    zoikonex_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Architecture doc §9 "Graceful degradation": set when a (mock) payment
    # failure is received, cleared on restoration. Incoming calls and number
    # ownership stay active regardless; outbound calling/video/purchases/AI
    # are gated once this passes (see app.billing.service.
    # assert_billing_not_suspended) - NULL means "no grace period in effect."
    grace_period_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ZoikoNexSyncEventType(str, enum.Enum):
    SUBSCRIPTION_SYNC = "subscription_sync"
    USAGE_SYNC = "usage_sync"
    PAYMENT_EVENT_RECEIVED = "payment_event_received"


class ZoikoNexSyncEvent(Base):
    """Outbound/inbound sync ledger for the mock ZoikoNex adapter - real
    even though the adapter itself is mocked, this is what a reconciliation
    job (Architecture doc §9's "daily reconciliation jobs... exceptions
    enter an operations queue") would actually compare against once a real
    ZoikoNex exists. See app.integrations.billing.zoikonex's docstring."""

    __tablename__ = "zoikonex_sync_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    event_type: Mapped[ZoikoNexSyncEventType] = mapped_column(
        Enum(ZoikoNexSyncEventType, name="zoikonex_sync_event_type_enum"), nullable=False, index=True
    )
    zoikonex_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
