from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_staff_role
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.integrations.telecom.twilio import TelecomError
from app.numbering.numbers.service import (
    NoStuckProvisioningError,
    NotDueForRenewalError,
    mark_number_renewed,
    release_stuck_provisioning,
    retry_provisioning,
)
from app.staff import service
from app.staff.models import PlatformStaff, PlatformStaffRole
from app.staff.schemas import AccountOverviewResponse, StaffLoginRequest, StaffTokenResponse
from app.usage.schemas import CallingRateResponse, UpsertCallingRateRequest
from app.usage.service import list_calling_rates, upsert_calling_rate

router = APIRouter(prefix="/staff", tags=["staff"])

# No signup endpoint here on purpose - staff accounts are provisioned
# internally (see app/seed.py), never via public self-registration.


@router.post("/login", response_model=StaffTokenResponse)
@limiter.limit("5/minute")
def login(request: Request, payload: StaffLoginRequest, db: Session = Depends(get_db)):
    staff = service.authenticate_staff(db, payload.email, payload.password)
    if not staff:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token(subject=staff.id, scope="staff")
    return StaffTokenResponse(access_token=token)


@router.get("/accounts", response_model=list[AccountOverviewResponse])
def list_accounts(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_accounts_overview(db)


@router.get("/numbers/search")
def search_numbers(
    q: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    if not q.strip():
        return []
    return service.search_numbers(db, q.strip())


@router.get("/numbers/stuck-provisioning")
def list_stuck_provisioning(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Diagnostic, not an approval action - any staff role can view it, same
    rationale as /ops/provider-status."""
    return service.list_stuck_provisioning(db)


@router.post("/numbers/{number_id}/retry-provisioning")
def retry_number_provisioning(
    number_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPPORT, PlatformStaffRole.SUPER_ADMIN)),
):
    try:
        number = retry_provisioning(db, staff.id, number_id)
    except NoStuckProvisioningError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return {"id": number.id, "e164": number.e164, "status": number.status}


@router.post("/numbers/{number_id}/release-provisioning")
def release_number_provisioning(
    number_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPPORT, PlatformStaffRole.SUPER_ADMIN)),
):
    try:
        number = release_stuck_provisioning(db, staff.id, number_id)
    except NoStuckProvisioningError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return {"id": number.id, "e164": number.e164, "status": number.status}


@router.get("/numbers/due-for-renewal")
def list_due_renewals(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Diagnostic worklist, not a billing run - see
    app.numbering.numbers.service.list_due_renewals's docstring. No real
    payment gateway exists yet to charge automatically."""
    return service.list_due_renewals(db)


@router.post("/numbers/{number_id}/mark-renewed")
def mark_number_renewed_route(
    number_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPPORT, PlatformStaffRole.SUPER_ADMIN)),
):
    try:
        number = mark_number_renewed(db, staff.id, number_id)
    except NotDueForRenewalError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return {"id": number.id, "e164": number.e164, "next_renewal_at": number.next_renewal_at}


@router.get("/calling-rates", response_model=list[CallingRateResponse])
def list_calling_rates_route(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return list_calling_rates(db)


@router.put("/calling-rates", response_model=CallingRateResponse)
def upsert_calling_rate_route(
    payload: UpsertCallingRateRequest,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_staff_role(PlatformStaffRole.SUPER_ADMIN)),
):
    # SUPER_ADMIN only - pricing changes are a platform-wide decision, not
    # a routine support action like the recovery endpoints above.
    return upsert_calling_rate(
        db, country=payload.country, price_per_minute_cents=payload.price_per_minute_cents,
        currency=payload.currency,
    )
