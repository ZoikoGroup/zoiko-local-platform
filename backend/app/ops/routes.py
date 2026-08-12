from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_capability
from app.numbering.identity.models import User
from app.observability import service as observability_service
from app.observability.schemas import (
    ErrorCountSummary,
    ErrorEventDetailResponse,
    ErrorEventResponse,
    ProviderCallTraceResponse,
    ProviderLatencySummary,
)
from app.ops import service
from app.ops.models import IncidentStatus, KillSwitchScope
from app.ops.schemas import (
    CreateIncidentRequest,
    IncidentResponse,
    KillSwitchResponse,
    SetKillSwitchRequest,
    StatusSubscriptionResponse,
    SyntheticCheckRunResponse,
    SyntheticCheckSummaryResponse,
    UpdateIncidentRequest,
)
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


@router.post("/synthetic-checks/run", response_model=list[SyntheticCheckRunResponse])
async def run_synthetic_checks(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Roadmap Month 5 launch-readiness gate: "synthetic call monitoring" -
    runs now, on demand, and persists the results (see
    app.ops.service.run_synthetic_checks's docstring for exactly what's
    covered). No scheduler exists in this codebase to run this
    automatically yet - same manual-trigger posture as the ZoikoNex
    reconciliation summary. Any staff role can trigger this, same
    diagnostic (not approval-action) posture as /provider-status."""
    return await service.run_synthetic_checks(db)


@router.get("/synthetic-checks", response_model=list[SyntheticCheckRunResponse])
def list_synthetic_checks(
    check_name: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_synthetic_check_runs(db, check_name=check_name, limit=limit)


@router.get("/synthetic-checks/summary", response_model=SyntheticCheckSummaryResponse)
def synthetic_checks_summary(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Most recent result per check, plus an overall pass/fail - the
    at-a-glance view; /synthetic-checks (plural, no /summary) has the full
    history for trend-spotting."""
    return service.get_synthetic_check_summary(db)


@router.get("/status")
async def public_status():
    """No auth - this is the customer-facing status page the marketing site
    links to. Deliberately separate from /provider-status: never leaks
    provider names or raw error detail, only named components and a plain
    operational/degraded status."""
    return await service.get_public_status()


@router.get("/incidents", response_model=list[IncidentResponse])
def list_incidents(limit: int = 50, db: Session = Depends(get_db)):
    """No auth - the status page's incident history, same posture as
    /status."""
    return service.list_incidents(db, limit=limit)


@router.post("/incidents", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
def create_incident(
    payload: CreateIncidentRequest,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("ops.manage_incidents")),
):
    return service.create_incident(
        db, title=payload.title, affected_service=payload.affected_service, impact_summary=payload.impact_summary,
    )


@router.put("/incidents/{incident_id}", response_model=IncidentResponse)
def update_incident(
    incident_id: str,
    payload: UpdateIncidentRequest,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("ops.manage_incidents")),
):
    try:
        return service.update_incident(
            db, incident_id, status=IncidentStatus(payload.status),
            impact_summary=payload.impact_summary, mitigation_summary=payload.mitigation_summary,
        )
    except service.IncidentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.IncidentAlreadyResolvedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.post("/incidents/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("ops.manage_incidents")),
):
    try:
        return service.resolve_incident(db, incident_id)
    except service.IncidentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.IncidentAlreadyResolvedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/status-subscription", response_model=StatusSubscriptionResponse, status_code=status.HTTP_201_CREATED)
def subscribe_to_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.subscribe_to_status(db, current_user.account_id, current_user.email)


@router.delete("/status-subscription", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe_from_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service.unsubscribe_from_status(db, current_user.account_id)


@router.get("/status-subscription/me", response_model=StatusSubscriptionResponse | None)
def get_my_status_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.get_status_subscription(db, current_user.account_id)


@router.get("/kill-switches", response_model=list[KillSwitchResponse])
def list_kill_switches(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Any staff role can view current kill-switch state (diagnostic, same
    posture as other status endpoints); activating/deactivating one is the
    sensitive action, gated below."""
    return service.list_kill_switches(db)


@router.post("/kill-switches/{scope}/activate", response_model=KillSwitchResponse)
def activate_kill_switch(
    scope: KillSwitchScope,
    payload: SetKillSwitchRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("ops.manage_kill_switches")),
):
    """SUPER_ADMIN only - halts new activity in this scope platform-wide
    (Commercial Billing Operating Standard doc §32.1). Does not touch
    activity already in flight or destroy any existing customer evidence -
    see PlatformKillSwitch's docstring."""
    return service.set_kill_switch(db, scope, True, actor=staff.id, reason=payload.reason)


@router.post("/kill-switches/{scope}/deactivate", response_model=KillSwitchResponse)
def deactivate_kill_switch(
    scope: KillSwitchScope,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("ops.manage_kill_switches")),
):
    """SUPER_ADMIN only."""
    return service.set_kill_switch(db, scope, False, actor=staff.id)
