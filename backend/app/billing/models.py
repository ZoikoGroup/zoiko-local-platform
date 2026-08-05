import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
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

    zoikonex_ref stays NULL until the ZoikoNex event contract is locked
    (see docs/Zoiko_Local_Phase_1_Engineering_Build_Roadmap.docx §15,
    "Lock ZoikoNex billing event contract and entitlement model" - still an
    open CTO action item, not yet done) - this table only tracks Zoiko
    Local's own entitlements, never talks to ZoikoNex.
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
    # Deliberately nullable, deliberately unused until a real ZoikoNex
    # connection exists - see class docstring.
    zoikonex_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
