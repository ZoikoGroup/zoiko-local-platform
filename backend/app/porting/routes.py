from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_capability
from app.numbering.identity.models import User
from app.porting import service
from app.porting.schemas import (
    PortingRequestCompleteRequest,
    PortingRequestCreate,
    PortingRequestRejectRequest,
    PortingRequestResponse,
    PortingRequestStaffResponse,
)
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/porting", tags=["porting"])


def _get_request_or_404(db: Session, request_id: str):
    request = service.get_porting_request(db, request_id)
    if request is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Porting request not found")
    return request


@router.post("/requests", response_model=PortingRequestResponse, status_code=201)
def create_request(
    payload: PortingRequestCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.submit_porting_request(
            db,
            account_id=current_user.account_id,
            requested_by_user_id=current_user.id,
            phone_number=payload.phone_number,
            country=payload.country,
            current_carrier=payload.current_carrier,
            carrier_account_number=payload.carrier_account_number,
            billing_name=payload.billing_name,
            billing_address=payload.billing_address,
            authorization_evidence_url=payload.authorization_evidence_url,
            target_completion_date=payload.target_completion_date,
        )
    except service.PortingRequestConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("/requests/me", response_model=list[PortingRequestResponse])
def my_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_my_porting_requests(db, current_user.account_id)


@router.post("/requests/{request_id}/cancel", response_model=PortingRequestResponse)
def cancel_request(
    request_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    request = _get_request_or_404(db, request_id)
    try:
        return service.cancel_porting_request(db, request, account_id=current_user.account_id, actor=current_user.id)
    except service.PortingRequestAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except service.PortingRequestConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.get("/requests", response_model=list[PortingRequestStaffResponse])
def list_all_requests(
    status: str | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_all_porting_requests(db, status=status)


@router.post("/requests/{request_id}/approve", response_model=PortingRequestResponse)
def approve_request(
    request_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("porting.review_request")),
):
    request = _get_request_or_404(db, request_id)
    try:
        return service.approve_porting_request(db, request, actor=staff.id)
    except service.PortingRequestConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/requests/{request_id}/reject", response_model=PortingRequestResponse)
def reject_request(
    request_id: str,
    payload: PortingRequestRejectRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("porting.review_request")),
):
    request = _get_request_or_404(db, request_id)
    try:
        return service.reject_porting_request(db, request, actor=staff.id, reason=payload.reason)
    except service.PortingRequestConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/requests/{request_id}/complete", response_model=PortingRequestResponse)
def complete_request(
    request_id: str,
    payload: PortingRequestCompleteRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("porting.review_request")),
):
    request = _get_request_or_404(db, request_id)
    try:
        return service.complete_porting_request(
            db, request, actor=staff.id, twilio_incoming_number_sid=payload.twilio_incoming_number_sid
        )
    except service.PortingRequestConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
