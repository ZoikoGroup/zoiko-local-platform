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
    price_per_minute_cents: int
    currency: str


class UpsertCallingRateRequest(BaseModel):
    country: str
    price_per_minute_cents: int
    currency: str = "USD"
