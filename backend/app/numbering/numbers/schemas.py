from datetime import datetime, time

from pydantic import BaseModel, ConfigDict


class RoutingConfigRequest(BaseModel):
    forwarding_number: str | None = None
    business_hours_start: time | None = None
    business_hours_end: time | None = None
    business_hours_timezone: str = "UTC"
    ai_receptionist_enabled: bool = False
    escalation_user_id: str | None = None


class SuspendNumberRequest(BaseModel):
    reason: str | None = None


class SetRingGroupRequest(BaseModel):
    destinations: list[str]


class RingGroupDestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    destination_number: str
    ring_order: int


class SetIVRMenuRequest(BaseModel):
    greeting: str
    options: dict[str, str]


class IVROptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    digit: str
    destination_number: str


class IVRMenuResponse(BaseModel):
    greeting: str | None
    options: list[IVROptionResponse]


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


class AssignNumberRequest(BaseModel):
    user_id: str | None = None  # None unassigns the number


class PhoneNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    e164: str
    country: str
    status: str
    account_id: str
    assigned_user_id: str | None
    reserved_until: datetime | None
    forwarding_number: str | None
    business_hours_start: time | None
    business_hours_end: time | None
    business_hours_timezone: str
    ai_receptionist_enabled: bool
    escalation_user_id: str | None
    next_renewal_at: datetime | None


class SupportedCountryResponse(BaseModel):
    code: str
    name: str
