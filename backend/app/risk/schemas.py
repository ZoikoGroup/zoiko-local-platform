from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.risk.models import RiskSignalType


class BlockedDestinationCreate(BaseModel):
    prefix: str
    reason: str


class BlockedDestinationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    prefix: str
    reason: str
    created_at: datetime


class RiskSignalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    signal_type: RiskSignalType
    detail: str
    created_at: datetime


class AccountRiskSummaryResponse(BaseModel):
    account_id: str
    score: int
    auto_suspend_threshold: int
    window_hours: int
    signals: list[RiskSignalResponse]


class AccountReinstateRequest(BaseModel):
    reason: str | None = None
