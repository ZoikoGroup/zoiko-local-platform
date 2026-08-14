from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.ops.models import KillSwitchScope
from app.risk.models import AccountRiskState, FraudCaseStatus, RiskSignalType


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
    risk_state: AccountRiskState
    auto_suspend_threshold: int
    review_threshold: int
    window_hours: int
    signals: list[RiskSignalResponse]


class AccountReinstateRequest(BaseModel):
    reason: str | None = None


class SetAccountRiskStateRequest(BaseModel):
    state: AccountRiskState
    # Mandatory, same "every override has actor, reason, timestamp and
    # evidence" posture as SetMarketActivationStatusRequest.reason.
    reason: str


class AccountKillSwitchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    scope: KillSwitchScope
    is_active: bool
    reason: str | None
    activated_by: str | None
    activated_at: datetime | None
    deactivated_at: datetime | None
    created_at: datetime


class SetAccountKillSwitchRequest(BaseModel):
    reason: str | None = None


class FraudRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    signal_type: RiskSignalType
    weight: int
    is_active: bool
    created_at: datetime


class FraudRuleUpsertRequest(BaseModel):
    weight: int
    is_active: bool = True


class FraudCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    score_at_open: int
    status: FraudCaseStatus
    resolved_by: str | None
    resolution_notes: str | None
    created_at: datetime
    resolved_at: datetime | None


class FraudCaseResolveRequest(BaseModel):
    status: FraudCaseStatus
    notes: str | None = None
