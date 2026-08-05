from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff
from app.observability import service as observability_service
from app.observability.schemas import (
    ErrorCountSummary,
    ErrorEventDetailResponse,
    ErrorEventResponse,
    ProviderCallTraceResponse,
    ProviderLatencySummary,
)
from app.ops import service
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/ops", tags=["ops"])


@router.get("/provider-status")
async def provider_status(_staff: PlatformStaff = Depends(get_current_staff)):
    """Any staff role can view this - it's diagnostic, not an approval
    action, so it doesn't need the SUPPORT/COMPLIANCE_OFFICER/SUPER_ADMIN
    segregation that KYC decisions do."""
    return {"providers": await service.get_provider_statuses()}


@router.get("/errors", response_model=list[ErrorEventResponse])
def list_errors(
    limit: int = 100,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Self-hosted error monitoring (Roadmap Month 5 launch-readiness gate) -
    every 5xx response and unhandled exception, most recent first. See
    app.core.error_logging.ErrorLoggingMiddleware for where these are written."""
    return observability_service.list_recent_errors(db, limit=limit)


@router.get("/errors/summary", response_model=list[ErrorCountSummary])
def error_summary(
    hours: int = 24,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Grouped by exception type/path/status - "is one thing failing
    repeatedly" is more actionable at a glance than a flat list."""
    return observability_service.error_counts_by_type(db, hours=hours)


@router.get("/errors/{error_id}", response_model=ErrorEventDetailResponse)
def error_detail(
    error_id: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Includes the full traceback - omitted from the list endpoint since
    it's large and rarely needed for every row at once."""
    event = observability_service.get_error_event(db, error_id)
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Error event not found")
    return event


@router.get("/traces", response_model=list[ProviderCallTraceResponse])
def list_traces(
    provider: str | None = None,
    request_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Self-hosted distributed tracing (Roadmap Month 5 launch-readiness
    gate) - every outbound Provider Gateway call, most recent first.
    Filter by request_id to see every external call one specific inbound
    request made (correlates with the X-Request-ID header and, for 5xx
    requests, the matching /ops/errors row)."""
    return observability_service.list_recent_provider_traces(
        db, provider=provider, request_id=request_id, limit=limit
    )


@router.get("/traces/summary", response_model=list[ProviderLatencySummary])
def trace_summary(
    hours: int = 24,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Grouped by provider+operation - avg/max latency and failure count
    over the window, the "what's slow or flaky right now" view."""
    return observability_service.provider_call_latency_summary(db, hours=hours)


@router.get("/status")
async def public_status():
    """No auth - this is the customer-facing status page the marketing site
    links to. Deliberately separate from /provider-status: never leaks
    provider names or raw error detail, only named components and a plain
    operational/degraded status."""
    return await service.get_public_status()
