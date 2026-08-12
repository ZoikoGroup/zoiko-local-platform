from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyntheticCheckRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    check_name: str
    success: bool
    duration_ms: float
    detail: str | None
    created_at: datetime


class SyntheticCheckSummaryResponse(BaseModel):
    overall_healthy: bool
    checks: list[SyntheticCheckRunResponse]


class IncidentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    affected_service: str
    status: str
    impact_summary: str
    mitigation_summary: str | None
    started_at: datetime
    resolved_at: datetime | None


class CreateIncidentRequest(BaseModel):
    title: str
    affected_service: str
    impact_summary: str


class UpdateIncidentRequest(BaseModel):
    status: str
    impact_summary: str | None = None
    mitigation_summary: str | None = None


class StatusSubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    is_active: bool
