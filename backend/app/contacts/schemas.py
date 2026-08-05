from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ContactCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone_number: str = Field(min_length=1, max_length=20)
    email: EmailStr | None = None
    notes: str | None = None


class ContactUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    phone_number: str = Field(min_length=1, max_length=20)
    email: EmailStr | None = None
    notes: str | None = None


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    name: str
    phone_number: str
    email: str | None
    notes: str | None
    created_by_user_id: str | None
    created_at: datetime
