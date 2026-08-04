from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PortingRequestCreate(BaseModel):
    phone_number: str
    country: str
    current_carrier: str
    carrier_account_number: str
    billing_name: str
    billing_address: str


class PortingRequestRejectRequest(BaseModel):
    reason: str | None = None


class PortingRequestCompleteRequest(BaseModel):
    twilio_incoming_number_sid: str


class PortingRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    phone_number: str
    country: str
    current_carrier: str
    carrier_account_number: str
    billing_name: str
    billing_address: str
    status: str
    rejection_reason: str | None
    twilio_incoming_number_sid: str | None
    created_number_id: str | None
    created_at: datetime


class PortingRequestStaffResponse(PortingRequestResponse):
    account_name: str
    account_owner_email: str
