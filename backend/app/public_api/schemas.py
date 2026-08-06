from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PublicNumberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    e164: str
    country: str
    status: str
    forwarding_number: str | None
    ai_receptionist_enabled: bool
    created_at: datetime


class PublicCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    direction: str
    from_number: str
    to_number: str
    status: str
    duration: int | None
    created_at: datetime


class PublicVoicemailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    from_number: str
    duration: int | None
    created_at: datetime


class PublicSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_type: str
    source_id: str
    summary: str
    urgency: str | None
    created_at: datetime


class PlaceCallRequest(BaseModel):
    to: str
    from_number: str
    message: str = "This is a call from Zoiko Local."


class PlaceCallResponse(BaseModel):
    sid: str
    status: str
    to: str


class CreateContactRequest(BaseModel):
    name: str
    phone_number: str
    email: str | None = None
    notes: str | None = None


class PublicContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    phone_number: str
    email: str | None
    notes: str | None
    created_at: datetime
