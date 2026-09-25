"""
Careers / Dynamic Jobs — Zoiko_Local_Careers_Dynamic_Jobs_Engineering_Standard
doc (ZL-ENG-CAREERS-001). Two routers:

- `public_router` (no auth at all) - what the separate marketing/careers
  React site (github.com/ZoikoGroup/zoiko-local-react, a different repo
  this backend does not touch) is meant to call. Genuinely public, same
  reason app.media.video's public_router is split from its authenticated
  one - mounting these under a router-wide auth dependency would break
  them regardless of in-route logic, since FastAPI resolves the full
  dependency tree before a route's own body runs.
- `staff_router` (capability-gated) - the "authenticated recruiting
  console" the doc calls for as the interim authority while no external
  ATS is connected.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.careers import service
from app.careers.schemas import (
    CreateRequisitionRequest,
    PublicFiltersResponse,
    PublicJobListResponse,
    PublicJobResponse,
    StaffJobResponse,
    TransitionStatusRequest,
    UpdateRequisitionRequest,
)
from app.core.database import get_db
from app.core.deps import require_capability
from app.core.rate_limit import limiter
from app.staff.models import PlatformStaff

public_router = APIRouter(prefix="/api/v1/careers", tags=["careers"])
staff_router = APIRouter(prefix="/staff/careers", tags=["careers"])


@public_router.get("/jobs", response_model=PublicJobListResponse)
@limiter.limit("60/minute")
def list_jobs(
    request: Request,
    q: str | None = None,
    team: str | None = None,
    location: str | None = None,
    work_model: str | None = None,
    employment_type: str | None = None,
    page: int = 1,
    page_size: int = 20,
    sort: str = "recent",
    db: Session = Depends(get_db),
):
    """Doc Table 6: search/list live jobs. Publishable (OPEN) jobs only -
    see service.PUBLIC_JOB_STATUSES."""
    page = max(1, page)
    page_size = max(1, min(page_size, 100))
    return service.list_public_jobs(
        db, q=q, team=team, location=location, work_model=work_model,
        employment_type=employment_type, page=page, page_size=page_size, sort=sort,
    )


@public_router.get("/jobs/{job_id_or_slug}", response_model=PublicJobResponse)
@limiter.limit("60/minute")
def get_job(request: Request, job_id_or_slug: str, db: Session = Depends(get_db)):
    """Doc Table 6: job detail. A non-public status (or a nonexistent id)
    returns 404 either way - see service.get_public_job's docstring on why
    that's the deliberate choice over a partial "closed role" page."""
    try:
        return service.get_public_job(db, job_id_or_slug)
    except service.JobNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@public_router.get("/filters", response_model=PublicFiltersResponse)
@limiter.limit("60/minute")
def get_filters(request: Request, db: Session = Depends(get_db)):
    """Doc Table 6: filter metadata, derived from real currently-OPEN jobs
    - never a hard-coded list (see service.list_public_filters)."""
    return service.list_public_filters(db)


@staff_router.get("", response_model=list[StaffJobResponse])
def list_requisitions(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("careers.manage_requisitions")),
):
    return service.list_all_jobs_for_staff(db)


@staff_router.post("", response_model=StaffJobResponse, status_code=status.HTTP_201_CREATED)
def create_requisition(
    payload: CreateRequisitionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("careers.manage_requisitions")),
):
    try:
        return service.create_requisition(db, actor=staff.id, **payload.model_dump())
    except service.InvalidSlugError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.RemoteLocationRequiredError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@staff_router.put("/{job_id}", response_model=StaffJobResponse)
def update_requisition(
    job_id: str,
    payload: UpdateRequisitionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("careers.manage_requisitions")),
):
    try:
        return service.update_requisition(db, job_id, actor=staff.id, **payload.model_dump())
    except service.JobNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.RemoteLocationRequiredError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@staff_router.post("/{job_id}/status", response_model=StaffJobResponse)
def transition_status(
    job_id: str,
    payload: TransitionStatusRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("careers.manage_requisitions")),
):
    try:
        return service.transition_status(db, job_id, new_status=payload.status, actor=staff.id, reason=payload.reason)
    except service.JobNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.InvalidStatusTransitionError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
