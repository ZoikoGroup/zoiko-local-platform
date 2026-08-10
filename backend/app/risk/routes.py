from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_staff_role
from app.numbering.numbers.service import reactivate_numbers_for_account_by_staff
from app.risk import service
from app.risk.models import RiskSignalType
from app.risk.schemas import (
    AccountReinstateRequest,
    AccountRiskSummaryResponse,
    BlockedDestinationCreate,
    BlockedDestinationResponse,
    FraudCaseResolveRequest,
    FraudCaseResponse,
    FraudRuleResponse,
    FraudRuleUpsertRequest,
)
from app.staff.models import PlatformStaff, PlatformStaffRole

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
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
):
    try:
        return service.add_blocked_destination(db, prefix=payload.prefix, reason=payload.reason, actor=staff.id)
    except service.DestinationRuleConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.delete("/blocked-destinations/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_blocked_destination(
    rule_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
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
    staff: PlatformStaff = Depends(
        require_staff_role(PlatformStaffRole.SUPER_ADMIN, PlatformStaffRole.COMPLIANCE_OFFICER)
    ),
):
    """Reverses a risk-engine auto-suspension (or any suspension) after
    human review - reactivates every SUSPENDED number on the account."""
    numbers = reactivate_numbers_for_account_by_staff(db, account_id, staff_id=staff.id, reason=payload.reason)
    return {"reactivated": [n.e164 for n in numbers]}


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
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
):
    return service.upsert_fraud_rule(
        db, signal_type=signal_type, weight=payload.weight, is_active=payload.is_active, actor=staff.id,
    )


@router.get("/fraud-cases", response_model=list[FraudCaseResponse])
def list_fraud_cases(
    case_status: str | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Review queue for accounts whose decayed risk score crossed
    REVIEW_THRESHOLD but not (yet) AUTO_SUSPEND_THRESHOLD - the earlywarning tier auto-suspension alone doesn't surface."""
    status_filter = service.FraudCaseStatus(case_status) if case_status else None
    return service.list_fraud_cases(db, status=status_filter)


@router.post("/fraud-cases/{case_id}/resolve", response_model=FraudCaseResponse)
def resolve_fraud_case(
    case_id: str,
    payload: FraudCaseResolveRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(
        require_staff_role(PlatformStaffRole.SUPER_ADMIN, PlatformStaffRole.COMPLIANCE_OFFICER)
    ),
):
    try:
        return service.resolve_fraud_case(
            db, case_id, status=payload.status, resolved_by=staff.id, notes=payload.notes,
        )
    except service.FraudCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (service.FraudCaseAlreadyResolvedError, service.InvalidFraudCaseResolutionError) as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
