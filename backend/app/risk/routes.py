from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_capability
from app.numbering.numbers.service import reactivate_numbers_for_account_by_staff
from app.risk import service
from app.risk.models import FraudCaseStatus
from app.risk.schemas import (
    AccountReinstateRequest,
    AccountRiskSummaryResponse,
    BlockedDestinationCreate,
    BlockedDestinationResponse,
    FraudCaseResponse,
    ResolveFraudCaseRequest,
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


@router.get("/fraud-cases", response_model=list[FraudCaseResponse])
def list_fraud_cases(
    case_status: FraudCaseStatus | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Roadmap doc §13 Risk Register "anomalous usage" review queue - any
    staff role can view it (diagnostic, same posture as the risk score
    view above); resolving one is the sensitive action, gated below."""
    return service.list_fraud_cases(db, status=case_status)


@router.post("/fraud-cases/{case_id}/resolve", response_model=FraudCaseResponse)
def resolve_fraud_case(
    case_id: str,
    payload: ResolveFraudCaseRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("risk.resolve_fraud_case")),
):
    try:
        return service.resolve_fraud_case(
            db, case_id, status=payload.status, actor=staff.id, notes=payload.notes
        )
    except service.FraudCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
