from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_capability
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.integrations.telecom.twilio import TelecomError
from app.numbering.numbers.schemas import SupportedCountryResponse, UpsertSupportedCountryRequest
from app.numbering.numbers.service import (
    NoStuckProvisioningError,
    NotDueForRenewalError,
    list_supported_countries,
    mark_number_renewed,
    release_stuck_provisioning,
    remove_supported_country,
    retry_provisioning,
    upsert_supported_country,
)
from app.staff import service
from app.staff.models import PlatformStaff, PlatformStaffRole
from app.staff.schemas import AccessMatrixEntryResponse, AccountOverviewResponse, StaffLoginRequest, StaffTokenResponse
from app.staff.service import LastGrantRemovalError, list_access_matrix
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
    staff: PlatformStaff = Depends(require_capability("numbers.manage_provisioning")),
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
    staff: PlatformStaff = Depends(require_capability("numbers.manage_provisioning")),
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
    staff: PlatformStaff = Depends(require_capability("numbers.manage_renewal")),
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
    _staff: PlatformStaff = Depends(require_capability("billing.manage_calling_rates")),
):
    # SUPER_ADMIN only (via the matrix) - pricing changes are a platform-
    # wide decision, not a routine support action like the recovery
    # endpoints above.
    return upsert_calling_rate(
        db, country=payload.country, price_per_minute_cents=payload.price_per_minute_cents,
        currency=payload.currency,
    )


@router.get("/countries", response_model=list[SupportedCountryResponse])
def list_supported_countries_route(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return list_supported_countries(db)


@router.put("/countries", response_model=SupportedCountryResponse)
def upsert_supported_country_route(
    payload: UpsertSupportedCountryRequest,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("numbers.manage_country_list")),
):
    # SUPER_ADMIN only - expanding the launch country list is a compliance/
    # commercial decision, same bar as a calling-rate change above.
    return upsert_supported_country(db, code=payload.code, name=payload.name, sort_order=payload.sort_order)


@router.delete("/countries/{code}", status_code=status.HTTP_204_NO_CONTENT)
def remove_supported_country_route(
    code: str,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(require_capability("numbers.manage_country_list")),
):
    remove_supported_country(db, code)
    return None


@router.get("/access-matrix", response_model=list[AccessMatrixEntryResponse])
def access_matrix_route(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Read-only visibility into the role x capability grid that actually
    gates every sensitive staff action (see app.staff.models.
    StaffCapabilityGrant's docstring) - the Commercial Billing Operating
    Standard doc's "formal RBAC/segregation-of-duties matrix" ask, made
    inspectable rather than only living as scattered require_capability(...)
    calls across route files. Any staff role can view it (diagnostic, not
    an approval action, same posture as /ops/provider-status) - granting
    or revoking a role's access is the sensitive action, gated below."""
    return list_access_matrix(db)


@router.put("/access-matrix/{capability}/{role}", status_code=status.HTTP_204_NO_CONTENT)
def grant_capability_route(
    capability: str,
    role: PlatformStaffRole,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability(service.MATRIX_MANAGEMENT_CAPABILITY)),
):
    service.grant_capability(db, capability=capability, role=role, actor=staff.id)
    return None


@router.delete("/access-matrix/{capability}/{role}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_capability_route(
    capability: str,
    role: PlatformStaffRole,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability(service.MATRIX_MANAGEMENT_CAPABILITY)),
):
    try:
        service.revoke_capability(db, capability=capability, role=role, actor=staff.id)
    except LastGrantRemovalError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    return None
