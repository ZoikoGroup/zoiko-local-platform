from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_staff_role
from app.core.security import create_access_token
from app.integrations.telecom.twilio import TelecomError
from app.numbering.numbers.service import NoStuckProvisioningError, release_stuck_provisioning, retry_provisioning
from app.staff import service
from app.staff.models import PlatformStaff, PlatformStaffRole
from app.staff.schemas import AccountOverviewResponse, StaffLoginRequest, StaffTokenResponse

router = APIRouter(prefix="/staff", tags=["staff"])

# No signup endpoint here on purpose - staff accounts are provisioned
# internally (see app/seed.py), never via public self-registration.


@router.post("/login", response_model=StaffTokenResponse)
def login(payload: StaffLoginRequest, db: Session = Depends(get_db)):
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
