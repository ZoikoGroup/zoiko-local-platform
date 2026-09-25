from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.careers.models import (
    PUBLIC_JOB_STATUSES,
    CareerCompensationState,
    CareerEmploymentType,
    CareerJob,
    CareerJobStatus,
    CareerWorkModel,
)
from app.core.ids import new_uuid
from app.integrations.cache.redis import cache_delete, cache_get, cache_set

_PUBLIC_JOBS_CACHE_KEY = "careers:public_jobs:v1"
# Doc §3.1 "Use short cache TTLs for public listings and purge/invalidate
# immediately on OPEN, PAUSED, FILLED, CLOSED, or CANCELED transitions" -
# every staff write below calls _invalidate_public_jobs_cache() directly
# (immediate purge), this TTL only bounds the worst case if that call were
# ever somehow missed, same posture as this codebase's other short-TTL
# caches (retention policies: 30s, supported countries: 60s).
_PUBLIC_JOBS_CACHE_TTL_SECONDS = 30


class JobNotFoundError(Exception):
    pass


class InvalidSlugError(Exception):
    pass


class RemoteLocationRequiredError(Exception):
    """Doc §4 "Remote" rule - a REMOTE role must state real eligible
    geography, never a placeholder, so a hybrid role can never accidentally
    read as fully remote."""


def _invalidate_public_jobs_cache() -> None:
    cache_delete(_PUBLIC_JOBS_CACHE_KEY)


def _serialize_job(job: CareerJob) -> dict:
    """Doc §2.1 "Minimum public job payload" / Table 7 field list - every
    field a candidate-facing page needs, nothing internal (no recruiter
    notes, no unpublished-job leakage - this function is only ever called
    on jobs already filtered to PUBLIC_JOB_STATUSES by the callers below)."""
    compensation = None
    if job.compensation_state == CareerCompensationState.DISCLOSED:
        compensation = {
            "min_minor_units": job.compensation_min_minor_units,
            "max_minor_units": job.compensation_max_minor_units,
            "currency": job.compensation_currency,
        }
    return {
        "job_id": job.id,
        "slug": job.slug,
        "status": job.status.value,
        "title": job.title,
        "hiring_org": job.hiring_org,
        "team": job.team,
        "employment_type": job.employment_type.value,
        "work_model": job.work_model.value,
        "job_locations": job.job_locations,
        "applicant_location_rules": job.applicant_location_rules,
        "date_posted": job.date_posted.isoformat(),
        "valid_through": job.valid_through.isoformat() if job.valid_through else None,
        "compensation_state": job.compensation_state.value,
        "compensation": compensation,
        "featured": job.featured,
        "apply_url": job.apply_url,
        "description": job.description,
        "last_verified_at": job.last_verified_at.isoformat(),
    }


def _load_public_jobs(db: Session) -> list[dict]:
    """The one query point for every public endpoint below - cached as a
    whole (doc's own P0 acceptance condition: "the role count always
    matches the current filtered result dataset," which is only reliably
    true if search/count/detail all resolve from the exact same dataset,
    not independently-cached slices of it)."""
    cached = cache_get(_PUBLIC_JOBS_CACHE_KEY)
    if cached is not None:
        return cached
    jobs = (
        db.query(CareerJob)
        .filter(CareerJob.status.in_(PUBLIC_JOB_STATUSES))
        .order_by(CareerJob.featured.desc(), CareerJob.date_posted.desc())
        .all()
    )
    result = [_serialize_job(j) for j in jobs]
    cache_set(_PUBLIC_JOBS_CACHE_KEY, result, ttl_seconds=_PUBLIC_JOBS_CACHE_TTL_SECONDS)
    return result


def list_public_jobs(
    db: Session, *, q: str | None = None, team: str | None = None, location: str | None = None,
    work_model: str | None = None, employment_type: str | None = None,
    page: int = 1, page_size: int = 20, sort: str = "recent",
) -> dict:
    """Doc Table 6 GET /api/v1/careers/jobs. Filters server-side over the
    cached OPEN-only dataset (doc §4 "Search" rule: "Do not filter a large
    static frontend array" - this is server/API filtered, the frontend
    only ever receives the already-filtered page). `total` is the count of
    the SAME filtered set the returned page is drawn from, satisfying
    "open-role count is computed from the same query and filters as the
    visible result set" (doc §4) without a second, independently-computed
    count query that could ever disagree."""
    jobs = _load_public_jobs(db)

    if team:
        jobs = [j for j in jobs if j["team"].lower() == team.lower()]
    if location:
        jobs = [j for j in jobs if location.lower() in j["job_locations"].lower()]
    if work_model:
        jobs = [j for j in jobs if j["work_model"] == work_model]
    if employment_type:
        jobs = [j for j in jobs if j["employment_type"] == employment_type]
    if q:
        needle = q.lower()
        jobs = [
            j for j in jobs
            if needle in j["title"].lower() or needle in j["team"].lower() or needle in j["description"].lower()
        ]

    if sort == "title":
        jobs = sorted(jobs, key=lambda j: j["title"].lower())
    # "recent" (default) is already the cached list's own order (featured
    # first, then most-recently-posted) - no re-sort needed.

    total = len(jobs)
    start = max(0, (page - 1) * page_size)
    page_jobs = jobs[start : start + page_size]
    return {"jobs": page_jobs, "total": total, "page": page, "page_size": page_size}


def get_public_job(db: Session, job_id_or_slug: str) -> dict:
    """Doc Table 6 GET /api/v1/careers/jobs/{job_id-or-slug}. Doc Table 8:
    a FILLED/CLOSED/CANCELED (or DRAFT/APPROVED/PAUSED) role must return a
    governed closed-role state or 404/410, never a normally-open-looking
    page - this raises JobNotFoundError for anything outside
    PUBLIC_JOB_STATUSES regardless of whether the row itself still exists,
    so the route can return a clean 404 either way. Reads from the same
    cached dataset list_public_jobs uses (not a separate live query), so a
    role that just left OPEN in this same 30s cache window can't be found
    here either - detail and list can never disagree."""
    jobs = _load_public_jobs(db)
    for job in jobs:
        if job["job_id"] == job_id_or_slug or job["slug"] == job_id_or_slug:
            return job
    raise JobNotFoundError(f"No public job matching {job_id_or_slug!r}")


def list_public_filters(db: Session) -> dict:
    """Doc Table 6 GET /api/v1/careers/filters - "Returns active teams,
    locations, work models and employment types derived from registries."
    Derived here from real distinct values across currently-OPEN jobs
    (this codebase's pragmatic stand-in for the doc's separate Registry
    tables - see CareerJob's own docstring), never a hard-coded list, so
    the filter UI can never offer an option with zero matching results."""
    jobs = _load_public_jobs(db)
    return {
        "teams": sorted({j["team"] for j in jobs}),
        "locations": sorted({j["job_locations"] for j in jobs}),
        "work_models": sorted({j["work_model"] for j in jobs}),
        "employment_types": sorted({j["employment_type"] for j in jobs}),
    }


# --- Staff-facing (authenticated recruiting console - doc §3.1's interim
# authority, since no external ATS is connected) ---


def list_all_jobs_for_staff(db: Session) -> list[CareerJob]:
    return db.query(CareerJob).order_by(CareerJob.created_at.desc()).all()


def get_job_for_staff(db: Session, job_id: str) -> CareerJob:
    job = db.query(CareerJob).filter(CareerJob.id == job_id).first()
    if job is None:
        raise JobNotFoundError(f"No requisition {job_id!r}")
    return job


def _validate_remote_geography(work_model: CareerWorkModel, job_locations: str) -> None:
    if work_model == CareerWorkModel.REMOTE and not job_locations.strip():
        raise RemoteLocationRequiredError(
            "A remote role must state real eligible geography in job_locations - never a placeholder."
        )


def create_requisition(
    db: Session, *, slug: str, title: str, hiring_org: str, team: str,
    employment_type: CareerEmploymentType, work_model: CareerWorkModel, job_locations: str,
    apply_url: str, actor: str, applicant_location_rules: str | None = None,
    compensation_state: CareerCompensationState = CareerCompensationState.NOT_REQUIRED,
    compensation_min_minor_units: int | None = None, compensation_max_minor_units: int | None = None,
    compensation_currency: str | None = None, featured: bool = False, description: str = "",
    valid_through: datetime | None = None,
) -> CareerJob:
    if not slug.strip():
        raise InvalidSlugError("slug must not be blank")
    existing = db.query(CareerJob).filter(CareerJob.slug == slug).first()
    if existing is not None:
        raise InvalidSlugError(f"slug {slug!r} is already in use")
    _validate_remote_geography(work_model, job_locations)

    job = CareerJob(
        id=new_uuid(), slug=slug, status=CareerJobStatus.DRAFT, title=title, hiring_org=hiring_org, team=team,
        employment_type=employment_type, work_model=work_model, job_locations=job_locations,
        applicant_location_rules=applicant_location_rules, apply_url=apply_url,
        compensation_state=compensation_state, compensation_min_minor_units=compensation_min_minor_units,
        compensation_max_minor_units=compensation_max_minor_units, compensation_currency=compensation_currency,
        featured=featured, description=description, valid_through=valid_through,
        date_posted=datetime.now(timezone.utc), last_verified_at=datetime.now(timezone.utc), created_by=actor,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    log_event(
        db, actor=actor, action="careers.requisition_created", target=f"career_job:{job.id}",
        after={"slug": job.slug, "title": job.title, "status": job.status.value},
    )
    return job


def update_requisition(db: Session, job_id: str, *, actor: str, **fields) -> CareerJob:
    """Partial update - only keys explicitly passed in `fields` are
    touched. Bumps last_verified_at on every edit (doc §3.1's freshness
    signal) and invalidates the public cache immediately, since any of
    these fields could be currently visible to candidates."""
    job = get_job_for_staff(db, job_id)
    before = {"title": job.title, "status": job.status.value}

    work_model = fields.get("work_model", job.work_model)
    job_locations = fields.get("job_locations", job.job_locations)
    _validate_remote_geography(work_model, job_locations)

    for key, value in fields.items():
        if value is not None and hasattr(job, key):
            setattr(job, key, value)
    job.last_verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    _invalidate_public_jobs_cache()
    log_event(
        db, actor=actor, action="careers.requisition_updated", target=f"career_job:{job.id}",
        before=before, after={"title": job.title, "status": job.status.value},
    )
    return job


# Doc §3 lifecycle table - the only transitions this console allows;
# anything not listed here (e.g. FILLED -> OPEN) must go through a fresh
# requisition rather than reopening a closed one, same "no silent
# resurrection" posture as this codebase's other status state machines.
_ALLOWED_TRANSITIONS: dict[CareerJobStatus, set[CareerJobStatus]] = {
    CareerJobStatus.DRAFT: {CareerJobStatus.APPROVED, CareerJobStatus.CANCELED},
    CareerJobStatus.APPROVED: {CareerJobStatus.OPEN, CareerJobStatus.CANCELED},
    CareerJobStatus.OPEN: {CareerJobStatus.PAUSED, CareerJobStatus.FILLED, CareerJobStatus.CLOSED, CareerJobStatus.CANCELED},
    CareerJobStatus.PAUSED: {CareerJobStatus.OPEN, CareerJobStatus.FILLED, CareerJobStatus.CLOSED, CareerJobStatus.CANCELED},
    CareerJobStatus.FILLED: set(),
    CareerJobStatus.CLOSED: set(),
    CareerJobStatus.CANCELED: set(),
}


class InvalidStatusTransitionError(Exception):
    pass


def transition_status(db: Session, job_id: str, *, new_status: CareerJobStatus, actor: str, reason: str | None = None) -> CareerJob:
    """Doc's central acceptance condition: "Changing a requisition status
    in the authoritative backend changes the website without a frontend
    code deployment" - the public cache invalidation below is what makes
    that true within _PUBLIC_JOBS_CACHE_TTL_SECONDS regardless of
    deployment state."""
    job = get_job_for_staff(db, job_id)
    if new_status not in _ALLOWED_TRANSITIONS.get(job.status, set()):
        raise InvalidStatusTransitionError(f"Cannot move a requisition from {job.status.value} to {new_status.value}")

    before_status = job.status
    job.status = new_status
    job.last_verified_at = datetime.now(timezone.utc)
    if before_status != CareerJobStatus.OPEN and new_status == CareerJobStatus.OPEN and job.date_posted is None:
        job.date_posted = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    _invalidate_public_jobs_cache()
    log_event(
        db, actor=actor, action="careers.requisition_status_changed", target=f"career_job:{job.id}",
        reason=reason, before={"status": before_status.value}, after={"status": new_status.value},
    )
    return job
