from datetime import datetime, time

from pydantic import BaseModel, ConfigDict


class RoutingConfigRequest(BaseModel):
    forwarding_number: str | None = None
    business_hours_start: time | None = None
    business_hours_end: time | None = None
    business_hours_timezone: str = "UTC"
    ai_receptionist_enabled: bool = False


class NumberSearchResult(BaseModel):
    phone_number: str
    locality: str | None = None
    region: str | None = None
    capabilities: dict | None = None


class ReserveNumberRequest(BaseModel):
    e164: str
    country: str


class PurchaseNumberRequest(BaseModel):
    e164: str


class PhoneNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    e164: str
    country: str
    status: str
    account_id: str
    reserved_until: datetime | None
    forwarding_number: str | None
    business_hours_start: time | None
    business_hours_end: time | None
    business_hours_timezone: str
    ai_receptionist_enabled: bool
