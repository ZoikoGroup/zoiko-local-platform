from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class ErrorEvent(Base):
    """Self-hosted error monitoring (Roadmap Month 5 launch-readiness gate) -
    every 5xx response and every truly unhandled exception, captured here so
    production failures are visible without a third-party APM account. See
    app.core.error_logging.ErrorLoggingMiddleware for where these get written.

    Deliberately NO foreign keys on account_id/user_id - logging an error
    must never itself fail because the referenced account/user row doesn't
    exist (unauthenticated request) or was deleted mid-request.
    """

    __tablename__ = "error_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Null when this row came from an explicit 5xx response (e.g. our own
    # `raise HTTPException(502, ...)` after a caught provider failure) rather
    # than a genuinely unhandled exception - there's no traceback to attach
    # in that case, only the fact that a 5xx happened.
    exception_type: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    exception_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    traceback: Mapped[str | None] = mapped_column(Text, nullable=True)
    account_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, index=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProviderCallTrace(Base):
    """Self-hosted distributed tracing (Architecture doc §11), scaled to what
    this platform actually needs: not full OpenTelemetry span export to a
    third-party backend, but a real, queryable record of every outbound
    Provider Gateway call - which provider, how long it took, whether it
    succeeded - tagged with the request_id of the inbound request that
    triggered it (see app.observability.service.current_request_id), so a
    slow or failing endpoint can be traced down to the specific external
    call responsible.

    Deliberately no foreign key on request_id - it's a correlation id, not
    a relationship, and most requests won't have a matching error_events
    row (only 5xx responses get one).
    """

    __tablename__ = "provider_call_traces"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
