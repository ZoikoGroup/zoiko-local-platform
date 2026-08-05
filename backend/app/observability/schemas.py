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
