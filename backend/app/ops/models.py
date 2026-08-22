import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class IncidentStatus(str, enum.Enum):
    INVESTIGATING = "investigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class Incident(Base):
    """Email Communications System doc's OPS domain ("Service Incident
    Declared" / "Incident Update" / "Incident Resolved") - the piece the
    public status page (app.ops.service.get_public_status) was missing:
    a persisted incident record subscribers actually get emailed about,
    not just a live provider-health snapshot. Deliberately scoped to
    real-time incidents only (declared as they're discovered, updated in
    place, resolved) - scheduled maintenance announcements, emergency-
    calling-specific notices, and regional/carrier-degradation framing are
    separate OPS templates seeded registry-only for now, since they'd need
    their own scheduling/classification concepts rather than fitting this
    same lifecycle."""

    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    affected_service: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus, name="incident_status_enum"), nullable=False, default=IncidentStatus.INVESTIGATING
    )
    impact_summary: Mapped[str] = mapped_column(Text, nullable=False)
    mitigation_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StatusSubscription(Base):
    """One account opted in to incident emails (OPS-009 "Status
    Subscription Confirmation"). Not every account gets incident emails by
    default - the doc frames this domain as opt-in ("Subscribed
    operations"), unlike the rest of the notification estate."""

    __tablename__ = "status_subscriptions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), unique=True, nullable=False, index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyntheticCheckRun(Base):
    """Roadmap Month 5 launch-readiness gate: "synthetic call monitoring" -
    distinct from provider_call_traces (real customer-triggered provider
    calls) and get_provider_statuses (a shallow "is the provider reachable"
    ping). This proactively exercises Zoiko Local's OWN inbound-webhook
    pipeline - signature verification against the real configured secret,
    database connectivity - the same way it would actually behave if a
    provider genuinely sent it an event, on a schedule independent of real
    traffic. Doesn't include a true end-to-end PSTN test call: the Twilio
    account here is trial-only and owns no real phone number to call (see
    docs note "Twilio trial account owns zero real phone numbers"), so
    that leg can't be exercised for real in this environment yet - see
    app.ops.service.run_synthetic_checks's docstring for exactly what each
    named check does and doesn't cover."""

    __tablename__ = "synthetic_check_runs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    check_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class KillSwitchScope(str, enum.Enum):
    NUMBER_PROVISIONING = "number_provisioning"
    OUTBOUND_CALLING = "outbound_calling"
    AI_PROCESSING = "ai_processing"
    PAYMENTS_BILLING = "payments_billing"


class PlatformKillSwitch(Base):
    """Commercial Billing Operating Standard doc §32.1 - "granular controls
    for new number provisioning, number release, new chargeable outbound
    calling... AI processing... payments/top-ups" to "stop new harm without
    destroying customer evidence or unrelated service" during an incident.
    One row per scope (upserted, not appended - see
    app.ops.service.set_kill_switch), so there's always exactly one current
    is_active state per scope with a full activate/deactivate audit trail
    via audit_event, not a growing table of toggle history here. Per §32.1,
    emergency-service obligations must not be blocked by a commercial kill
    switch - satisfied by construction today since no scope here gates any
    real emergency-calling code path (see EmergencyDisclosureRequiredError's
    docstring on why real E911 doesn't exist in this codebase yet)."""

    __tablename__ = "platform_kill_switches"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    scope: Mapped[KillSwitchScope] = mapped_column(
        Enum(KillSwitchScope, name="kill_switch_scope_enum"), unique=True, nullable=False, index=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activated_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Commercial Billing Operating Standard doc §U2 - an emergency
    # commercial override must be "time-bounded" (named approver, reason,
    # scope, expiry...). NULL means no expiry was set at activation time
    # (still allowed - not every real incident has a known resolution ETA
    # up front) but every switch that DOES set one gets auto-deactivated
    # by expire_overdue_kill_switches (app.ops.scheduled_reconciliation)
    # and treated as inactive immediately by assert_kill_switch_not_active
    # even before that sweep runs, so a forgotten switch can't silently
    # stay active past its own stated expiry.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
