from datetime import datetime

from pydantic import BaseModel, EmailStr, ConfigDict, Field


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
    role: str
    is_active: bool
    created_at: datetime


class CreateStaffMemberRequest(BaseModel):
    email: EmailStr
    # Same min_length=8 bar as customer signup (numbering/identity/schemas.py's
    # _PasswordField) - if anything, staff accounts warrant it more: they
    # include SUPER_ADMIN (see authenticate_staff's own docstring on why
    # staff auth is treated as more sensitive than customer auth).
    password: str = Field(min_length=8, max_length=128)
    role: str


class AccountOverviewResponse(BaseModel):
    id: str
    name: str
    account_type: str
    owner_email: str | None
    member_count: int
    number_count: int
    billing_classification: str
    billing_source: str
    is_test: bool
    legal_hold: bool
    legal_hold_reference: str | None
    created_at: datetime


class UpdateAccountBillingClassificationRequest(BaseModel):
    billing_classification: str
    billing_source: str


class SetAccountTestFlagRequest(BaseModel):
    is_test: bool
    reason: str


class SetAccountLegalHoldRequest(BaseModel):
    on: bool
    reference: str | None = None


class AccessMatrixEntryResponse(BaseModel):
    capability: str
    roles: list[str]
