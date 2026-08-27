from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CreateErasureRequestRequest(BaseModel):
    notes: str | None = None


class ResolveErasureRequestRequest(BaseModel):
    status: str  # "COMPLETED" or "REJECTED"
    resolution_notes: str | None = None


class ErasureRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    requested_by: str
    status: str
    notes: str | None
    resolved_by: str | None
    resolution_notes: str | None
    created_at: datetime
    resolved_at: datetime | None
