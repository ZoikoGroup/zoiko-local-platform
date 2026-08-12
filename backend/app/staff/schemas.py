from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict


class StaffLoginRequest(BaseModel):
    email: EmailStr
    password: str


class StaffTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class StaffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    is_active: bool


class AccountOverviewResponse(BaseModel):
    id: str
    name: str
    account_type: str
    owner_email: str | None
    member_count: int
    number_count: int
    billing_classification: str
    billing_source: str
    created_at: datetime


class UpdateAccountBillingClassificationRequest(BaseModel):
    billing_classification: str
    billing_source: str


class AccessMatrixEntryResponse(BaseModel):
    capability: str
    roles: list[str]
