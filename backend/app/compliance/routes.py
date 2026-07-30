from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.compliance import service
from app.compliance.schemas import (
    ComplianceCaseCreate,
    ComplianceCaseResponse,
    ComplianceRuleResponse,
)
from app.core.database import get_db
from app.core.deps import get_current_user
from app.numbering.identity.models import User

router = APIRouter(prefix="/compliance", tags=["compliance"])


@router.get("/rules", response_model=list[ComplianceRuleResponse])
def list_rules(country: str, db: Session = Depends(get_db)):
    return service.get_active_rules(db, country)


@router.post("/cases", response_model=ComplianceCaseResponse, status_code=201)
def create_case(
    payload: ComplianceCaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.open_compliance_case(
        db,
        account_id=current_user.account_id,
        jurisdiction=payload.jurisdiction,
        requirement_type=payload.requirement_type,
        number_id=payload.number_id,
        actor=current_user.id,
    )


@router.get("/cases/me", response_model=list[ComplianceCaseResponse])
def my_cases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_cases_for_account(db, current_user.account_id)
