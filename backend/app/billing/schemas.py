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
    grace_period_ends_at: datetime | None


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


class SimulatePaymentEventRequest(BaseModel):
    account_id: str
    event_type: str  # "payment_failed" | "payment_retry" | "payment_restored"


class ZoikoNexSyncEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    event_type: str
    zoikonex_ref: str | None
    payload: dict
    created_at: datetime


class ZoikoNexReconciliationSummary(BaseModel):
    total_subscriptions: int
    synced_subscriptions: int
    unsynced_subscriptions: int
    total_usage_events: int
    synced_usage_events: int
    unsynced_usage_events: int
