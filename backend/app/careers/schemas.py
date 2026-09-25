from datetime import datetime

from pydantic import BaseModel

from app.careers.models import CareerCompensationState, CareerEmploymentType, CareerJobStatus, CareerWorkModel


class CompensationResponse(BaseModel):
    min_minor_units: int | None
    max_minor_units: int | None
    currency: str | None


class PublicJobResponse(BaseModel):
    """Doc §2.1 "Minimum public job payload" / Table 7."""

    job_id: str
    slug: str
    status: str
    title: str
    hiring_org: str
    team: str
    employment_type: str
    work_model: str
    job_locations: str
    applicant_location_rules: str | None
    date_posted: str
    valid_through: str | None
    compensation_state: str
    compensation: CompensationResponse | None
    featured: bool
    apply_url: str
    description: str
    last_verified_at: str


class PublicJobListResponse(BaseModel):
    jobs: list[PublicJobResponse]
    total: int
    page: int
    page_size: int


class PublicFiltersResponse(BaseModel):
    teams: list[str]
    locations: list[str]
    work_models: list[str]
    employment_types: list[str]


class StaffJobResponse(BaseModel):
    """Same shape as PublicJobResponse but for the staff console - every
    status (including DRAFT/APPROVED/PAUSED/FILLED/CLOSED/CANCELED), not
    just what's currently public."""

    model_config = {"from_attributes": True}

    id: str
    slug: str
    status: CareerJobStatus
    title: str
    hiring_org: str
    team: str
    employment_type: CareerEmploymentType
    work_model: CareerWorkModel
    job_locations: str
    applicant_location_rules: str | None
    date_posted: datetime
    valid_through: datetime | None
    compensation_state: CareerCompensationState
    compensation_min_minor_units: int | None
    compensation_max_minor_units: int | None
    compensation_currency: str | None
    featured: bool
    apply_url: str
    description: str
    last_verified_at: datetime
    created_by: str
    created_at: datetime
    updated_at: datetime


class CreateRequisitionRequest(BaseModel):
    slug: str
    title: str
    team: str
    employment_type: CareerEmploymentType
    work_model: CareerWorkModel
    job_locations: str
    apply_url: str
    hiring_org: str = "Zoiko Local"
    applicant_location_rules: str | None = None
    compensation_state: CareerCompensationState = CareerCompensationState.NOT_REQUIRED
    compensation_min_minor_units: int | None = None
    compensation_max_minor_units: int | None = None
    compensation_currency: str | None = None
    featured: bool = False
    description: str = ""
    valid_through: datetime | None = None


class UpdateRequisitionRequest(BaseModel):
    """Every field optional - only what's provided gets changed
    (service.update_requisition skips anything left None)."""

    title: str | None = None
    hiring_org: str | None = None
    team: str | None = None
    employment_type: CareerEmploymentType | None = None
    work_model: CareerWorkModel | None = None
    job_locations: str | None = None
    applicant_location_rules: str | None = None
    apply_url: str | None = None
    compensation_state: CareerCompensationState | None = None
    compensation_min_minor_units: int | None = None
    compensation_max_minor_units: int | None = None
    compensation_currency: str | None = None
    featured: bool | None = None
    description: str | None = None
    valid_through: datetime | None = None


class TransitionStatusRequest(BaseModel):
    status: CareerJobStatus
    reason: str | None = None
