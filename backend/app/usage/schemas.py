from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    quantity: float
    unit: str
    country_band: str | None
    estimated_cost_cents: int | None
    created_at: datetime


class CallingRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    country: str
    destination_country: str | None = None
    price_per_minute_cents: int
    currency: str


class UpsertCallingRateRequest(BaseModel):
    country: str
    price_per_minute_cents: int
    currency: str = "USD"
    destination_country: str | None = None


class NumberRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    country: str
    number_type: str
    recurring_price_cents: int
    currency: str
    is_placeholder: bool


class UpsertNumberRateRequest(BaseModel):
    country: str
    number_type: str = "local"
    recurring_price_cents: int
    currency: str = "USD"
    is_placeholder: bool = False


class AIUsageRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    overage_price_cents_per_minute: int
    currency: str
    is_placeholder: bool


class UpsertAIUsageRateRequest(BaseModel):
    overage_price_cents_per_minute: int
    currency: str = "USD"
    is_placeholder: bool = False


class UsageDisputeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    usage_event_id: str
    reason: str
    status: str
    raised_by: str
    resolved_by: str | None
    resolution_notes: str | None
    created_at: datetime
    resolved_at: datetime | None


class OpenUsageDisputeRequest(BaseModel):
    usage_event_id: str
    reason: str


class ResolveUsageDisputeRequest(BaseModel):
    status: str
    notes: str | None = None
    new_estimated_cost_cents: int | None = None


class UsageAdjustmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    usage_event_id: str
    dispute_id: str | None
    previous_estimated_cost_cents: int | None
    new_estimated_cost_cents: int
    reason: str
    actor: str
    created_at: datetime
