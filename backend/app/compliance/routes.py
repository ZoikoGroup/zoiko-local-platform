from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from app.compliance import service
from app.compliance.schemas import (
    CaseRejectRequest,
    ComplianceCaseCreate,
    ComplianceCaseResponse,
    ComplianceCaseStaffResponse,
    ComplianceRuleResponse,
    DocumentDownloadUrl, 
    KYCVerificationStart,
)
from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_capability, require_writer
from app.integrations.kyc.stripe_identity import KYCError, construct_webhook_event
from app.integrations.storage.s3 import StorageError
from app.numbering.identity.models import User
from app.staff.models import PlatformStaff

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
async def submit_document(
    case_id: str,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    case = _get_case_or_404(db, case_id)
    if case.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    data = await file.read()
    try:
        return service.submit_document(
            db,
            case,
            document_type=document_type,
            filename=file.filename or "document",
            content_type=file.content_type or "application/octet-stream",
            data=data,
            actor=current_user.id,
        )
    except (service.UnsupportedDocumentTypeError, service.DocumentTooLargeError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.get("/cases/{case_id}/documents/{document_index}/download-url", response_model=DocumentDownloadUrl)
def get_document_download_url(
    case_id: str,
    document_index: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    case = _get_case_or_404(db, case_id)
    if case.account_id != current_user.account_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your case")

    try:
        url = service.get_document_download_url(case, document_index)
    except service.DocumentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return {"url": url}


@router.get("/staff/cases/{case_id}/documents/{document_index}/download-url", response_model=DocumentDownloadUrl)
def staff_get_document_download_url(
    case_id: str,
    document_index: int,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    case = _get_case_or_404(db, case_id)
    try:
        url = service.get_document_download_url(case, document_index)
    except service.DocumentNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except StorageError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return {"url": url}


@router.post("/cases/{case_id}/kyc/start", response_model=KYCVerificationStart)
def start_kyc(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
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
    staff: PlatformStaff = Depends(require_capability("compliance.review_case")),
):
    case = _get_case_or_404(db, case_id)
    return service.approve_case(db, case, actor=staff.id)


@router.post("/cases/{case_id}/reject", response_model=ComplianceCaseResponse)
def reject_case(
    case_id: str,
    payload: CaseRejectRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("compliance.review_case")),
):
    case = _get_case_or_404(db, case_id)
    return service.reject_case(db, case, actor=staff.id, reason=payload.reason)


@router.post("/cases/sweep-expired", response_model=list[ComplianceCaseResponse])
def sweep_expired_cases(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("compliance.review_case")),
):
    """Same manual-trigger-plus-external-cron pattern as POST
    /billing/zoikonex/reconciliation/run - meant to be hit periodically by
    a scheduled job, with a staff-triggerable route for on-demand runs
    between them. Marks any PENDING case past its expires_at as EXPIRED."""
    return service.sweep_expired_compliance_cases(db)
