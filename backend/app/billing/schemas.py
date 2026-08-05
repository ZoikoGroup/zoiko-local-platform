from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PlanResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    plan_code: str
    name: str
    max_numbers: int
    max_team_seats: int
    monthly_voice_minutes: int
    monthly_video_minutes: int
    monthly_ai_summaries: int
    trial_days: int


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plan_code: str
    status: str
    trial_ends_at: datetime | None
    current_period_start: datetime
    current_period_end: datetime
    zoikonex_ref: str | None


class ChangePlanRequest(BaseModel):
    plan_code: str


class UsageResourceSummary(BaseModel):
    resource: str
    used: float
    limit: int


class UsageSummaryResponse(BaseModel):
    plan_code: str
    plan_name: str
    status: str
    trial_ends_at: datetime | None
    current_period_start: datetime
    current_period_end: datetime
    resources: list[UsageResourceSummary]
