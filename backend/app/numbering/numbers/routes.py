from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.billing.service import BillingSuspendedError, NumberQuotaExceededError
from app.core.database import get_db
from app.core.deps import get_current_user, require_admin, require_writer
from app.integrations.billing import stripe_checkout
from app.integrations.telecom.twilio import TelecomError
from app.numbering.identity.models import User
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
    SubmitNumberEligibilityEvidenceRequest,
    SupportedCountryResponse,
    SuspendNumberRequest,
)
from app.numbering.numbers.service import (
    ComplianceRequiredError,
    EmergencyDisclosureRequiredError,
    InvalidAreaCodeError,
    NumberConflictError,
    NumberEligibilityCaseNotFoundError,
    NumberEligibilityRequiredError,
    UnsupportedCountryError,
)

router = APIRouter(prefix="/numbers", tags=["numbers"])


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
        return service.search_numbers(db, country, number_type=number_type, area_code=area_code)
    except UnsupportedCountryError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except InvalidAreaCodeError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


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
    except (ComplianceRequiredError, EmergencyDisclosureRequiredError, NumberEligibilityRequiredError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


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


@router.post("/{e164}/checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(
    e164: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Real Stripe Checkout (test mode) - the customer-facing way to buy a
    number now goes through here instead of calling POST /purchase
    directly. Commercial Billing Operating Standard doc's canonical chain
    (eligibility before any charge) - quota/billing-suspended/emergency-
    disclosure/KYC/eligibility are all checked here, BEFORE Stripe is ever
    contacted, so a customer who still needs documents is never charged
    while waiting on them (see service.create_number_purchase_checkout_
    session's docstring). POST /purchase still exists and still runs
    unchanged - it's what the payment webhook below calls once Stripe
    confirms payment, and what a retry after case approval uses for an
    already-paid number stuck in COMPLIANCE_PENDING."""
    try:
        return service.create_number_purchase_checkout_session(db, current_user.account_id, e164)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except service.NonCommercialAccountError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except (NumberQuotaExceededError, BillingSuspendedError) as e:
        raise HTTPException(status_code=status.HTTP_402_PAYMENT_REQUIRED, detail=str(e)) from e
    except (ComplianceRequiredError, EmergencyDisclosureRequiredError, NumberEligibilityRequiredError) as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except stripe_checkout.PaymentError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/payments/webhook", status_code=status.HTTP_204_NO_CONTENT)
async def stripe_payment_webhook(request: Request, db: Session = Depends(get_db)):
    """Real inbound Stripe -> Zoiko Local webhook. Unauthenticated by user
    session (Stripe isn't one of our users) - trust comes entirely from
    the Stripe-Signature header, same posture as the Stripe Identity
    webhook at compliance/routes.py and the ZoikoNex webhook at
    billing/routes.py."""
    body = await request.body()
    signature = request.headers.get("Stripe-Signature")

    try:
        event = stripe_checkout.construct_webhook_event(body, signature)
    except stripe_checkout.PaymentError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        # StripeObject supports dict-style [] / in but not .get() - see
        # this route's test coverage for the AttributeError that using
        # .get() here would raise.
        metadata = session["metadata"] if "metadata" in session else {}
        e164 = metadata["e164"] if "e164" in metadata else None
        account_id = metadata["account_id"] if "account_id" in metadata else None
        payment_intent_id = session["payment_intent"] if "payment_intent" in session else None
        if e164 and account_id:
            service.complete_number_purchase_from_checkout(
                db, e164=e164, account_id=account_id, payment_intent_id=payment_intent_id
            )

    return None


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
            payload.whatsapp_enabled, payload.sms_enabled,
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
