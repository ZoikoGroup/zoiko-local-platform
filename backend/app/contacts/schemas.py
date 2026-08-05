from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ContactCreateRequest(BaseModel):
    name: str
    phone_number: str
    email: str | None = None
    notes: str | None = None


class ContactUpdateRequest(BaseModel):
    name: str
    phone_number: str
    email: str | None = None
    notes: str | None = None


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    phone_number: str
    email: str | None
    notes: str | None
    created_at: datetime


class ContactHistoryEntry(BaseModel):
    type: str  # "call" | "voicemail" | "receptionist_call"
    id: str
    direction: str | None = None
    status: str | None = None
    duration: int | None = None
    summary: str | None = None
    recording_url: str | None = None
    created_at: datetime
