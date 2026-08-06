from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.billing.service import BillingSuspendedError, NumberQuotaExceededError
from app.core.database import get_db
from app.core.deps import get_current_user, require_admin, require_writer
from app.integrations.telecom.twilio import TelecomError
from app.numbering.identity.models import User
from app.numbering.numbers import service
from app.numbering.numbers.schemas import (
    AssignNumberRequest,
    NumberSearchResult,
    PhoneNumberResponse,
    PurchaseNumberRequest,
    ReserveNumberRequest,
    RingGroupDestinationResponse,
    RoutingConfigRequest,
    SetRingGroupRequest,
    SuspendNumberRequest,
)
from app.numbering.numbers.service import (
    ComplianceRequiredError,
    EmergencyDisclosureRequiredError,
    NumberConflictError,
)

router = APIRouter(prefix="/numbers", tags=["numbers"])


@router.get("/search", response_model=list[NumberSearchResult])
def search_numbers(
    country: str,
    number_type: str = "local",
    area_code: str | None = None,
    current_user: User = Depends(get_current_user),
):
    try:
        return service.search_numbers(country, number_type=number_type, area_code=area_code)
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/reserve", response_model=PhoneNumberResponse, status_code=status.HTTP_201_CREATED)
def reserve_number(
    payload: ReserveNumberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.reserve_number(db, current_user.account_id, payload.e164, payload.country)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/purchase", response_model=PhoneNumberResponse)
def purchase_number(
    payload: PurchaseNumberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.purchase_number(db, current_user.account_id, payload.e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except (NumberQuotaExceededError, BillingSuspendedError) as e:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)) from e
    except (ComplianceRequiredError, EmergencyDisclosureRequiredError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.get("", response_model=list[PhoneNumberResponse])
def list_numbers(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_account_numbers(db, current_user.account_id, user=current_user)


@router.put("/{e164}/assign", response_model=PhoneNumberResponse)
def assign_number(
    e164: str,
    payload: AssignNumberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.assign_number(
            db, account_id=current_user.account_id, e164=e164,
            user_id=payload.user_id, actor=current_user.id,
        )
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/{e164}/suspend", response_model=PhoneNumberResponse)
def suspend_number(
    e164: str,
    payload: SuspendNumberRequest = SuspendNumberRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.suspend_number(db, current_user, e164, reason=payload.reason)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@router.post("/{e164}/cancel", response_model=PhoneNumberResponse)
def cancel_number(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.cancel_number(db, current_user, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{e164}/sync-webhook", response_model=PhoneNumberResponse)
def sync_webhook(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.sync_webhook(db, current_user, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.put("/{e164}/routing", response_model=PhoneNumberResponse)
def configure_routing(
    e164: str,
    payload: RoutingConfigRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.configure_routing(
            db, current_user, e164,
            payload.forwarding_number, payload.business_hours_start,
            payload.business_hours_end, payload.business_hours_timezone,
            payload.ai_receptionist_enabled, payload.escalation_user_id,
        )
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.put("/{e164}/ring-group", response_model=list[RingGroupDestinationResponse])
def set_ring_group(
    e164: str,
    payload: SetRingGroupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        return service.set_ring_group(db, current_user, e164, payload.destinations)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.RingGroupTooLargeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.get("/{e164}/ring-group", response_model=list[RingGroupDestinationResponse])
def get_ring_group(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        service.assert_owns_number(db, current_user, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return service.list_ring_group(db, e164)
