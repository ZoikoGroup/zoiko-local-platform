from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.compliance import service
from app.compliance.schemas import (
    CaseRejectRequest,
    ComplianceCaseCreate,
    ComplianceCaseResponse,
    ComplianceRuleResponse,
    DocumentSubmit,
)
from app.core.database import get_db
from app.core.deps import get_current_user, require_admin
from app.numbering.identity.models import User

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _get_case_or_404(db: Session, case_id: str):
    case = service.get_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compliance case not found")
    return case


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


@router.post("/cases/{case_id}/documents", response_model=ComplianceCaseResponse)
def submit_document(
    case_id: str,
    payload: DocumentSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_case_or_404(db, case_id)
    if case.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    return service.submit_document(
        db, case, document_type=payload.document_type, reference=payload.reference, actor=current_user.id
    )


@router.post("/cases/{case_id}/approve", response_model=ComplianceCaseResponse)
def approve_case(
    case_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    case = _get_case_or_404(db, case_id)
    return service.approve_case(db, case, actor=admin.id)


@router.post("/cases/{case_id}/reject", response_model=ComplianceCaseResponse)
def reject_case(
    case_id: str,
    payload: CaseRejectRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    case = _get_case_or_404(db, case_id)
    return service.reject_case(db, case, actor=admin.id, reason=payload.reason)
