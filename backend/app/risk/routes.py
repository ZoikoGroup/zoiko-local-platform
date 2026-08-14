from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_capability
from app.numbering.numbers.service import reactivate_numbers_for_account_by_staff
from app.ops.models import KillSwitchScope
from app.risk import service
from app.risk.models import FraudCaseStatus, RiskSignalType
from app.risk.schemas import (
    AccountKillSwitchResponse,
    AccountReinstateRequest,
    AccountRiskSummaryResponse,
    BlockedDestinationCreate,
    BlockedDestinationResponse,
    FraudCaseResolveRequest,
    FraudCaseResponse,
    FraudRuleResponse,
    FraudRuleUpsertRequest,
    SetAccountKillSwitchRequest,
    SetAccountRiskStateRequest,
)
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/risk", tags=["risk"])


@router.get("/blocked-destinations", response_model=list[BlockedDestinationResponse])
def list_blocked_destinations(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_blocked_destinations(db)


@router.post("/blocked-destinations", response_model=BlockedDestinationResponse, status_code=status.HTTP_201_CREATED)
def add_blocked_destination(
    payload: BlockedDestinationCreate,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.manage_blocked_destinations")),
):
    try:
        return service.add_blocked_destination(db, prefix=payload.prefix, reason=payload.reason, actor=staff.id)
    except service.DestinationRuleConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/blocked-destinations/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_blocked_destination(
    rule_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.manage_blocked_destinations")),
):
    service.remove_blocked_destination(db, rule_id, actor=staff.id)


@router.get("/accounts/{account_id}/score", response_model=AccountRiskSummaryResponse)
def get_account_risk_score(
    account_id: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-only view of an account's rolling risk score (Roadmap doc §13
    Risk Register: "account risk scoring") - the same signals and threshold
    that drive automatic suspension, visible before or after it fires."""
    return service.get_account_risk_summary(db, account_id)


@router.put("/accounts/{account_id}/risk-state", response_model=AccountRiskSummaryResponse)
def set_account_risk_state(
    account_id: str,
    payload: SetAccountRiskStateRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.manage_account_risk_state")),
):
    """Production Readiness Standard doc "Rule of Authority" - a human can
    always override the fraud engine's trial-abuse step-up tier in either
    direction, with a mandatory reason. Same SUPER_ADMIN/COMPLIANCE_OFFICER
    bar as the reinstate/resolve-fraud-case actions below, since this can
    just as easily loosen an account's limits as tighten them."""
    try:
        service.set_account_risk_state(
            db, account_id, state=payload.state, actor=staff.id, reason=payload.reason,
        )
    except service.AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return service.get_account_risk_summary(db, account_id)


@router.post("/accounts/{account_id}/reinstate", status_code=status.HTTP_200_OK)
def reinstate_account_numbers(
    account_id: str,
    payload: AccountReinstateRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.reinstate_account")),
):
    """Reverses a risk-engine auto-suspension (or any suspension) after
    human review - reactivates every SUSPENDED number on the account."""
    numbers = reactivate_numbers_for_account_by_staff(db, account_id, staff_id=staff.id, reason=payload.reason)
    return {"reactivated": [n.e164 for n in numbers]}


@router.get("/accounts/{account_id}/kill-switches", response_model=list[AccountKillSwitchResponse])
def list_account_kill_switches(
    account_id: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Production Readiness Standard Table 15's "Tenant" kill-switch scope -
    diagnostic view, same posture as the platform-wide kill-switch list."""
    return service.list_account_kill_switches(db, account_id)


@router.post("/accounts/{account_id}/kill-switches/{scope}/activate", response_model=AccountKillSwitchResponse)
def activate_account_kill_switch(
    account_id: str,
    scope: KillSwitchScope,
    payload: SetAccountKillSwitchRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("ops.manage_kill_switches")),
):
    """SUPER_ADMIN only - halts new activity in this scope for ONE account,
    without suspending it outright. Same capability as the platform-wide
    switch (app.ops.routes.activate_kill_switch)."""
    return service.set_account_kill_switch(db, account_id, scope, True, actor=staff.id, reason=payload.reason)


@router.post("/accounts/{account_id}/kill-switches/{scope}/deactivate", response_model=AccountKillSwitchResponse)
def deactivate_account_kill_switch(
    account_id: str,
    scope: KillSwitchScope,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("ops.manage_kill_switches")),
):
    """SUPER_ADMIN only."""
    return service.set_account_kill_switch(db, account_id, scope, False, actor=staff.id)


@router.get("/fraud-rules", response_model=list[FraudRuleResponse])
def list_fraud_rules(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-tunable weights for the fraud scoring model (Architecture doc
    Phase 4 "proprietary fraud models") - a signal type with no row here is
    still active, at its built-in default weight (see risk/service.py's
    _DEFAULT_WEIGHTS)."""
    return service.list_fraud_rules(db)


@router.put("/fraud-rules/{signal_type}", response_model=FraudRuleResponse)
def upsert_fraud_rule(
    signal_type: RiskSignalType,
    payload: FraudRuleUpsertRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.manage_fraud_rules")),
):
    return service.upsert_fraud_rule(
        db, signal_type=signal_type, weight=payload.weight, is_active=payload.is_active, actor=staff.id,
    )


@router.get("/fraud-cases", response_model=list[FraudCaseResponse])
def list_fraud_cases(
    case_status: FraudCaseStatus | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Review queue for accounts whose decayed risk score crossed
    REVIEW_THRESHOLD but not (yet) AUTO_SUSPEND_THRESHOLD - the early-
    warning tier auto-suspension alone doesn't surface. Any staff role can
    view it (diagnostic, same posture as the risk score view above);
    resolving one is the sensitive action, gated below."""
    return service.list_fraud_cases(db, status=case_status)


@router.post("/fraud-cases/{case_id}/resolve", response_model=FraudCaseResponse)
def resolve_fraud_case(
    case_id: str,
    payload: FraudCaseResolveRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.resolve_fraud_case")),
):
    try:
        return service.resolve_fraud_case(
            db, case_id, status=payload.status, actor=staff.id, notes=payload.notes,
        )
    except service.FraudCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (service.FraudCaseAlreadyResolvedError, service.InvalidFraudCaseResolutionError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
