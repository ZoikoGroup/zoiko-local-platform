import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class CareerJobStatus(str, enum.Enum):
    """Zoiko_Local_Careers_Dynamic_Jobs_Engineering_Standard doc §3 lifecycle
    table. DRAFT/APPROVED are never public. Only OPEN is publicly listed/
    applyable. PAUSED disables Apply and is deliberately excluded from
    public search too - the doc's own wording ("Never appear as normally
    open") plus "public visibility follows explicit policy" (a policy this
    codebase doesn't have yet) makes full exclusion the safe default rather
    than inventing a partial-visibility UI Product hasn't specified.
    FILLED/CLOSED/CANCELED are removed from search and job-detail
    immediately (404), matching the doc's "or 404/410 per SEO policy"
    option - a dedicated "governed closed-role page" is a Product/SEO copy
    decision, not required for the P0 "stop hard-coding vacancy data" goal
    this pass is scoped to."""

    DRAFT = "draft"
    APPROVED = "approved"
    OPEN = "open"
    PAUSED = "paused"
    FILLED = "filled"
    CLOSED = "closed"
    CANCELED = "canceled"


class CareerEmploymentType(str, enum.Enum):
    FULL_TIME = "full_time"
    PART_TIME = "part_time"
    CONTRACT = "contract"
    OTHER = "other"


class CareerWorkModel(str, enum.Enum):
    ON_SITE = "on_site"
    HYBRID = "hybrid"
    REMOTE = "remote"


class CareerCompensationState(str, enum.Enum):
    """Doc §4 "Compensation" rule - visible pay copy and structured data
    must come from the same record, and an unavailable range must never be
    invented. NOT_REQUIRED covers jurisdictions with no pay-transparency
    mandate for this role; WITHHELD_PENDING_DISCLOSURE blocks the range
    fields from ever being shown even if populated, for a role whose
    disclosure requirement hasn't been cleared yet."""

    DISCLOSED = "disclosed"
    NOT_REQUIRED = "not_required"
    WITHHELD_PENDING_DISCLOSURE = "withheld_pending_disclosure"


# Doc §3.1: only OPEN is ever shown publicly - see CareerJobStatus's own
# docstring for why PAUSED is excluded too rather than partially visible.
PUBLIC_JOB_STATUSES = {CareerJobStatus.OPEN}


class CareerJob(Base):
    """Zoiko_Local_Careers_Dynamic_Jobs_Engineering_Standard doc (ZL-ENG-
    CAREERS-001) - the authoritative requisition record this codebase
    didn't have at all before this (confirmed: no careers/requisition/
    ATS/JobPosting code existed anywhere in this repo). The doc's own
    "primary stack" line names Django for this, but Zoiko Local's actual
    backend is FastAPI throughout (see CLAUDE.md's "only two folders hold
    application code" rule) - a separate Django service just to manage job
    postings would be real, unjustified architectural drift for what this
    doc itself says can be "an authenticated recruiting console as the
    interim authority" when no external ATS is connected (confirmed: none
    is). Built as a normal FastAPI domain module instead, gated the same
    way every other staff-managed resource in this codebase already is.

    hiring_org/team/job_locations/applicant_location_rules are plain
    strings rather than separate normalized "Registry" tables the doc
    describes (Hiring Organization Registry, Org/Team Registry, Location
    Registry) - a deliberate P0 scope call: the doc's actual hard
    requirement is "nothing hard-coded in the Next.js page, everything
    resolves from the API" (§Definition of done), which plain
    staff-editable fields on one row already satisfy. GET /careers/filters
    derives its options from real distinct values across current OPEN
    rows, not a hard-coded list, so the anti-hard-coding requirement is
    met without the extra normalized-table machinery - promote these to
    real registries later only if/when multiple hiring orgs or a
    compensation-jurisdiction rules engine actually need one."""

    __tablename__ = "career_jobs"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    status: Mapped[CareerJobStatus] = mapped_column(
        Enum(CareerJobStatus, name="career_job_status_enum"), nullable=False, default=CareerJobStatus.DRAFT
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    hiring_org: Mapped[str] = mapped_column(String(200), nullable=False, default="Zoiko Local")
    team: Mapped[str] = mapped_column(String(120), nullable=False)
    employment_type: Mapped[CareerEmploymentType] = mapped_column(
        Enum(CareerEmploymentType, name="career_employment_type_enum"), nullable=False
    )
    work_model: Mapped[CareerWorkModel] = mapped_column(
        Enum(CareerWorkModel, name="career_work_model_enum"), nullable=False
    )
    # Doc §4 "Remote" rule - "Use only when role is genuinely remote in the
    # stated geography; hybrid roles must not be labeled fully remote" is
    # enforced by keeping work_model and job_locations as two distinct
    # fields (never inferring one from the other) plus the staff-facing
    # validation in service.py that a REMOTE role must state real eligible
    # geography here, not a placeholder.
    job_locations: Mapped[str] = mapped_column(String(300), nullable=False)
    applicant_location_rules: Mapped[str | None] = mapped_column(String(300), nullable=True)
    date_posted: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    valid_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    compensation_state: Mapped[CareerCompensationState] = mapped_column(
        Enum(CareerCompensationState, name="career_compensation_state_enum"),
        nullable=False, default=CareerCompensationState.NOT_REQUIRED,
    )
    compensation_min_minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compensation_max_minor_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    compensation_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    featured: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    apply_url: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Doc §3.1 "Store last_verified_at ... on every publishable job
    # projection" - bumped on every staff edit/status transition
    # (service.py), acting as the freshness signal the doc wants even
    # without a real external ATS sync to verify against yet.
    last_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
