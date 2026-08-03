from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ComplianceRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    country: str
    requirement_type: str
    required_documents: list
    is_active: bool


class ComplianceCaseCreate(BaseModel):
    jurisdiction: str
    requirement_type: str
    number_id: str | None = None


class DocumentSubmit(BaseModel):
    document_type: str
    reference: str


class CaseRejectRequest(BaseModel):
    reason: str | None = None


class ComplianceCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    number_id: str | None
    jurisdiction: str
    requirement_type: str
    status: str
    documents: list
    expires_at: datetime | None
    created_at: datetime
    kyc_inquiry_id: str | None


class ComplianceCaseStaffResponse(ComplianceCaseResponse):
    account_name: str
    account_owner_email: str


class KYCVerificationStart(BaseModel):
    inquiry_id: str
    verification_url: str
