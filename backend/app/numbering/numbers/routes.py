import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_admin, require_writer
from app.integrations.telecom.twilio import TelecomError
from app.numbering.identity.models import User
from app.ops.service import KillSwitchTrippedError
from app.risk.service import AccountKillSwitchTrippedError
from app.numbering.numbers import service
from app.numbering.numbers.schemas import (
    AssignNumberRequest,
    CheckoutSessionResponse,
    IVRMenuResponse,
    NumberEligibilityCaseResponse,
    NumberSearchResult,
    PhoneNumberResponse,
    PurchaseNumberRequest,
    ReserveNumberRequest,
    RingGroupDestinationResponse,
    RoutingConfigRequest,
    SetIVRMenuRequest,
    SetRingGroupRequest,
    SubmitEligibilityBundleRequest,
    SubmitNumberEligibilityEvidenceRequest,
    SupportedCountryResponse,
    SuspendNumberRequest,
)
from app.numbering.numbers.service import (
    ComplianceRequiredError,
    EmergencyDisclosureRequiredError,
    InvalidAreaCodeError,
    MarketNotActivatedError,
    NumberConflictError,
    NumberDocumentTooLargeError,
    NumberDocumentTypeUnsupportedError,
    NumberEligibilityCaseNotFoundError,
    NumberEligibilityDocumentRequiredError,
    NumberEligibilityRequiredError,
    TestAccountRestrictedError,
    UnsupportedCountryError,
)

router = APIRouter(prefix="/numbers", tags=["numbers"])
logger = logging.getLogger("zoiko.numbers")


def _telecom_error_response(e: TelecomError) -> HTTPException:
    """Provider Gateway's whole premise is that a customer only ever knows
    "Zoiko Local" exists - never Twilio, never Vonage, never a raw HTTP
    status line from either. Confirmed live: a Vonage 401 ("Vonage number
    purchase request failed: Client error '401 Unauthorized' for url
    'https://rest.nexmo.com/number/buy'...") was reaching the checkout
    screen verbatim. The real detail still goes to the server log for
    support/debugging - it's just never handed to the customer."""
    logger.error("Telecom provider error surfaced to a customer-facing route: %s", e)
    return HTTPException(
        status_code=502,
        detail="We couldn't complete this right now. Please try again shortly, or contact support if it continues.",
    )


@router.get("/countries", response_model=list[SupportedCountryResponse])
def list_supported_countries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_supported_countries(db)


@router.get("/search", response_model=list[NumberSearchResult])
def search_numbers(
    country: str,
    number_type: str = "local",
    area_code: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return service.search_numbers(
            db, country, account_id=current_user.account_id, number_type=number_type, area_code=area_code,
        )
    except UnsupportedCountryError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except MarketNotActivatedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except InvalidAreaCodeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except TelecomError as e:
        raise _telecom_error_response(e) from e


@router.post("/reserve", response_model=PhoneNumberResponse, status_code=status.HTTP_201_CREATED)
def reserve_number(
    payload: ReserveNumberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.reserve_number(
            db, current_user.account_id, payload.e164, payload.country, payload.number_type,
        )
    except UnsupportedCountryError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except MarketNotActivatedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
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
    except MarketNotActivatedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    # NumberQuotaExceededError/BillingSuspendedError no longer caught here -
    # both subclass EntitlementError and are handled by the global
    # entitlement_error_handler in app.main, which already returns 402 plus
    # a machine-readable code.
    except (ComplianceRequiredError, EmergencyDisclosureRequiredError, NumberEligibilityRequiredError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TelecomError as e:
        raise _telecom_error_response(e) from e
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e
    except AccountKillSwitchTrippedError as e:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e)) from e


@router.get("/eligibility-cases", response_model=list[NumberEligibilityCaseResponse])
def list_own_eligibility_cases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Customer-facing view of eligibility cases opened for this account's
    numbers - surfaces which specific number purchase is blocked on a
    market-eligibility case, and its current status/review notes."""
    return service.list_eligibility_cases_for_account(db, current_user.account_id)


@router.post("/eligibility-cases/{case_id}/evidence", response_model=NumberEligibilityCaseResponse)
def submit_eligibility_evidence(
    case_id: str,
    payload: SubmitNumberEligibilityEvidenceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        return service.submit_number_eligibility_evidence(
            db, case_id, payload.evidence, account_id=current_user.account_id, actor=current_user.id,
        )
    except NumberEligibilityCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/eligibility-cases/{case_id}/documents", response_model=NumberEligibilityCaseResponse)
async def submit_eligibility_document(
    case_id: str,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Real supporting-document upload (government ID, passport, etc.) for
    a number-eligibility case - our own copy, stored the same way
    compliance/routes.py's document upload does. See submit_eligibility_
    bundle below for actually submitting this to Twilio's real review."""
    data = await file.read()
    try:
        return service.submit_number_eligibility_document(
            db, case_id, account_id=current_user.account_id, document_type=document_type,
            filename=file.filename or "document", content_type=file.content_type or "application/octet-stream",
            data=data, actor=current_user.id,
        )
    except NumberEligibilityCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except (NumberDocumentTypeUnsupportedError, NumberDocumentTooLargeError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.post("/eligibility-cases/{case_id}/submit-bundle", response_model=NumberEligibilityCaseResponse)
def submit_eligibility_bundle(
    case_id: str,
    payload: SubmitEligibilityBundleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Submits this case's most recently uploaded document, plus the given
    identity attributes, as a real Regulatory Bundle to Twilio for review
    - genuinely creates real Twilio resources (EndUser, SupportingDocument,
    Bundle), not a mock."""
    try:
        return service.submit_number_eligibility_bundle(
            db, case_id, account_id=current_user.account_id, end_user_attributes=payload.end_user_attributes,
            end_user_type=payload.end_user_type, actor=current_user.id,
        )
    except NumberEligibilityCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NumberEligibilityDocumentRequiredError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/eligibility-cases/{case_id}/sync-bundle-status", response_model=NumberEligibilityCaseResponse)
def sync_eligibility_bundle_status(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """On-demand check against Twilio's real bundle review status - see
    service.sync_number_eligibility_bundle_status's docstring for why this
    is poll-on-request rather than a webhook."""
    try:
        return service.sync_number_eligibility_bundle_status(
            db, case_id, account_id=current_user.account_id, actor=current_user.id,
        )
    except NumberEligibilityCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NumberEligibilityDocumentRequiredError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/{e164}/checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """The customer-facing way to buy a number goes through here instead of
    calling POST /purchase directly. Commercial Billing Operating Standard
    doc's canonical chain (eligibility before any charge) - quota/billing-
    suspended/emergency-disclosure/KYC/eligibility are all checked here.
    Architecture doc §9: a number's cost is now recorded as a pending
    charge and provisioned immediately - see service.create_number_
    purchase_checkout_session's docstring for why this no longer redirects
    to a standalone Stripe Checkout. POST /purchase still exists unchanged
    - it's what a retry after case approval uses for an already-eligible
    number stuck in COMPLIANCE_PENDING."""
    try:
        return service.create_number_purchase_checkout_session(db, current_user.account_id, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except MarketNotActivatedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TestAccountRestrictedError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except service.NonCommercialAccountError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    # NumberQuotaExceededError/BillingSuspendedError - see the comment at
    # the /purchase route above; handled by the global entitlement handler.
    except (ComplianceRequiredError, EmergencyDisclosureRequiredError, NumberEligibilityRequiredError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TelecomError as e:
        # Included-first-number path calls purchase_number() (and therefore
        # the telecom provider) synchronously - unlike search_numbers's
        # route, this had no handler for a provider failure here at all,
        # so it crashed as an unhandled 500 instead of a clean customer-
        # facing error (confirmed live: primary hit a Twilio trial-account
        # number cap, failover to Vonage then hit a 401 - Vonage account
        # not yet authorized to purchase).
        raise _telecom_error_response(e) from e


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
        raise _telecom_error_response(e) from e


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
        raise _telecom_error_response(e) from e


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
            payload.whatsapp_enabled, payload.sms_enabled,
            payload.escalation_phone_number,
        )
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ComplianceRequiredError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
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


@router.put("/{e164}/ivr", response_model=IVRMenuResponse)
def set_ivr_menu(
    e164: str,
    payload: SetIVRMenuRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        number, options = service.set_ivr_menu(db, current_user, e164, payload.greeting, payload.options)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.InvalidIVROptionError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return IVRMenuResponse(greeting=number.ivr_greeting, options=options)


@router.get("/{e164}/ivr", response_model=IVRMenuResponse)
def get_ivr_menu(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        service.assert_owns_number(db, current_user, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    number, options = service.get_ivr_menu(db, e164)
    return IVRMenuResponse(greeting=number.ivr_greeting if number else None, options=options)


@router.delete("/{e164}/ivr", status_code=status.HTTP_204_NO_CONTENT)
def clear_ivr_menu(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        service.clear_ivr_menu(db, current_user, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
