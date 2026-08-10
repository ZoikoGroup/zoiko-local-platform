from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SyntheticCheckRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    check_name: str
    success: bool
    duration_ms: float
    detail: str | None
    created_at: datetime


class SyntheticCheckSummaryResponse(BaseModel):
    overall_healthy: bool
    checks: list[SyntheticCheckRunResponse]
