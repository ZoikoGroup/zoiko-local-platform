from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ErrorEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    request_id: str
    method: str
    path: str
    status_code: int
    exception_type: str | None
    exception_message: str | None
    account_id: str | None
    user_id: str | None
    created_at: datetime


class ErrorEventDetailResponse(ErrorEventResponse):
    traceback: str | None


class ErrorCountSummary(BaseModel):
    exception_type: str | None
    path: str
    status_code: int
    count: int


class ProviderCallTraceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    request_id: str | None
    provider: str
    operation: str
    duration_ms: float
    success: bool
    error_detail: str | None
    created_at: datetime


class ProviderLatencySummary(BaseModel):
    provider: str
    operation: str
    count: int
    avg_duration_ms: float
    max_duration_ms: float
    failure_count: int
