from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


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
