from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.compliance import service
from app.compliance.schemas import (
    CaseRejectRequest,
    ComplianceCaseCreate,
    ComplianceCaseResponse,
    ComplianceCaseStaffResponse,
    ComplianceRuleResponse,
    DocumentSubmit,
    KYCVerificationStart,
)
from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_staff_role
from app.integrations.kyc.stripe_identity import KYCError, construct_webhook_event
from app.numbering.identity.models import User
from app.staff.models import PlatformStaff, PlatformStaffRole

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
    current_user: User = Depends(require_admin),
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


@router.get("/cases", response_model=list[ComplianceCaseStaffResponse])
def list_all_cases(
    status: str | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_all_cases(db, status=status)


@router.post("/cases/{case_id}/documents", response_model=ComplianceCaseResponse)
def submit_document(
    case_id: str,
    payload: DocumentSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    case = _get_case_or_404(db, case_id)
    if case.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    return service.submit_document(
        db, case, document_type=payload.document_type, reference=payload.reference, actor=current_user.id
    )


@router.post("/cases/{case_id}/kyc/start", response_model=KYCVerificationStart)
def start_kyc(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_case_or_404(db, case_id)
    if case.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    try:
        return service.start_kyc_verification(db, case, actor=current_user.id)
    except service.KYCAlreadyApprovedError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except KYCError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.post("/webhooks/stripe-identity", status_code=204)
async def stripe_identity_webhook(request: Request, db: Session = Depends(get_db)):
    body = await request.body()
    signature = request.headers.get("Stripe-Signature", "")

    try:
        event = construct_webhook_event(body, signature)
    except KYCError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

    if event["type"].startswith("identity.verification_session."):
        session = event["data"]["object"]
        last_error = session["last_error"]
        service.handle_stripe_identity_webhook(
            db,
            session_id=session["id"],
            status=session["status"],
            last_error_reason=last_error["reason"] if last_error else None,
        )
    return None


@router.post("/cases/{case_id}/approve", response_model=ComplianceCaseResponse)
def approve_case(
    case_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(
        require_staff_role(PlatformStaffRole.COMPLIANCE_OFFICER, PlatformStaffRole.SUPER_ADMIN)
    ),
):
    case = _get_case_or_404(db, case_id)
    return service.approve_case(db, case, actor=staff.id)


@router.post("/cases/{case_id}/reject", response_model=ComplianceCaseResponse)
def reject_case(
    case_id: str,
    payload: CaseRejectRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(
        require_staff_role(PlatformStaffRole.COMPLIANCE_OFFICER, PlatformStaffRole.SUPER_ADMIN)
    ),
):
    case = _get_case_or_404(db, case_id)
    return service.reject_case(db, case, actor=staff.id, reason=payload.reason)
