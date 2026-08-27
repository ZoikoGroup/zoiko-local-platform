from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, require_capability
from app.core.rate_limit import limiter
from app.core.security import create_access_token
from app.integrations.telecom.twilio import TelecomError
from app.numbering.numbers.schemas import (
    NumberEligibilityCaseResponse,
    NumberEligibilityRuleResponse,
    ResolveNumberEligibilityCaseRequest,
    SetMarketActivationStatusRequest,
    SupportedCountryResponse,
    UpdateCountryRegistryFieldsRequest,
    UpsertNumberEligibilityRuleRequest,
    UpsertSupportedCountryRequest,
)
from app.numbering.numbers.models import MarketActivationStatus, NumberEligibilityCaseStatus
from app.numbering.numbers.service import (
    MissingLegalSignoffError,
    NoStuckProvisioningError,
    NotDueForRenewalError,
    NumberConflictError,
    NumberEligibilityCaseNotFoundError,
    UnsupportedCountryError,
    approve_number_eligibility_case,
    list_all_eligibility_cases,
    list_number_eligibility_rules,
    list_supported_countries,
    mark_number_renewed,
    reinstate_caller_identity,
    reject_number_eligibility_case,
    release_stuck_provisioning,
    remove_number_eligibility_rule,
    remove_supported_country,
    retry_provisioning,
    revoke_caller_identity,
    seed_market_release_registry,
    set_market_activation_status,
    update_country_registry_fields,
    upsert_number_eligibility_rule,
    upsert_supported_country,
)
from app.staff import service
from app.staff.models import PlatformStaff, PlatformStaffRole
from app.staff.schemas import (
    AccessMatrixEntryResponse,
    AccountOverviewResponse,
    SetAccountLegalHoldRequest,
    SetAccountTestFlagRequest,
    StaffLoginRequest,
    StaffTokenResponse,
    UpdateAccountBillingClassificationRequest,
)
from app.staff.service import LastGrantRemovalError, list_access_matrix
from app.usage.schemas import (
    AIUsageRateResponse,
    CallingRateResponse,
    NumberRateResponse,
    UpsertAIUsageRateRequest,
    UpsertCallingRateRequest,
    UpsertNumberRateRequest,
)
from app.usage.service import (
    get_ai_usage_rate,
    list_calling_rates,
    list_number_rates,
    upsert_ai_usage_rate,
    upsert_calling_rate,
    upsert_number_rate,
)

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


@router.put("/accounts/{account_id}/billing-classification", response_model=AccountOverviewResponse)
def update_account_billing_classification(
    account_id: str,
    payload: UpdateAccountBillingClassificationRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("staff.manage_billing_classification")),
):
    """Commercial Billing Operating Standard doc P0 blocker - marks an
    account as something other than the COMMERCIAL_STANDALONE default
    (DEMO, SANDBOX, PARTNER_SPONSORED, etc.). SUPER_ADMIN-only capability -
    misclassifying an account either lets it dodge real billing it should
    have, or exposes a demo/test account to live charges."""
    from app.numbering.identity.models import AccountBillingClassification, AccountBillingSource

    try:
        classification = AccountBillingClassification(payload.billing_classification)
        source = AccountBillingSource(payload.billing_source)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    try:
        service.update_account_billing_classification(
            db, account_id, billing_classification=classification, billing_source=source, actor=staff.id,
        )
    except service.AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    return service.get_account_overview(db, account_id)


@router.put("/accounts/{account_id}/test-flag", response_model=AccountOverviewResponse)
def set_account_test_flag(
    account_id: str,
    payload: SetAccountTestFlagRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("accounts.manage_test_flag")),
):
    """Flags/unflags an account is_test - lets it bypass the CONTROLLED_BETA/
    INTERNAL_TEST market-activation gate for testing purchases, at the cost
    of also being blocked from real Stripe/ZoikoNex billing while flagged.
    SUPER_ADMIN-only (see the accounts.manage_test_flag grant) - a
    platform-wide decision, not a routine support action."""
    try:
        service.set_account_test_flag(
            db, account_id, is_test=payload.is_test, actor=staff.id, reason=payload.reason,
        )
    except service.AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    return service.get_account_overview(db, account_id)


@router.put("/accounts/{account_id}/legal-hold", response_model=AccountOverviewResponse)
def set_account_legal_hold(
    account_id: str,
    payload: SetAccountLegalHoldRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("accounts.manage_legal_hold")),
):
    """SUPER_ADMIN-only. Architecture doc §10 "legal hold model" - while
    active, blocks app.retention.service's purge sweeps from deleting this
    account's recordings/voicemail regardless of retention-window expiry."""
    try:
        service.set_account_legal_hold(db, account_id, on=payload.on, reference=payload.reference, actor=staff.id)
    except service.AccountNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.LegalHoldRequiresReferenceError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    return service.get_account_overview(db, account_id)


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


class CallerIdentityActionRequest(BaseModel):
    reason: str | None = None


@router.post("/numbers/{number_id}/caller-identity/revoke")
def revoke_caller_identity_route(
    number_id: str,
    payload: CallerIdentityActionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_caller_identity")),
):
    """Fraud/abuse response (Commercial Billing Operating Standard doc §R6)
    - blocks the number from outbound presentation without touching its
    billing/ownership status (see CallerIdentity's docstring)."""
    try:
        identity = revoke_caller_identity(db, number_id, staff_id=staff.id, reason=payload.reason)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return {"phone_number_id": identity.phone_number_id, "status": identity.status}


@router.post("/numbers/{number_id}/caller-identity/reinstate")
def reinstate_caller_identity_route(
    number_id: str,
    payload: CallerIdentityActionRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_caller_identity")),
):
    """Reversal of revoke - a false positive or resolved dispute shouldn't
    need a brand new number purchase to restore calling."""
    try:
        identity = reinstate_caller_identity(db, number_id, staff_id=staff.id, reason=payload.reason)
    except NumberConflictError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return {"phone_number_id": identity.phone_number_id, "status": identity.status}


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
    staff: PlatformStaff = Depends(require_capability("billing.manage_calling_rates")),
):
    # SUPER_ADMIN only (via the matrix) - pricing changes are a platform-
    # wide decision, not a routine support action like the recovery
    # endpoints above.
    return upsert_calling_rate(
        db, country=payload.country, price_per_minute_cents=payload.price_per_minute_cents,
        currency=payload.currency, destination_country=payload.destination_country, actor=staff.id,
    )


@router.get("/number-rates", response_model=list[NumberRateResponse])
def list_number_rates_route(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return list_number_rates(db)


@router.put("/number-rates", response_model=NumberRateResponse)
def upsert_number_rate_route(
    payload: UpsertNumberRateRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.manage_number_rates")),
):
    # SUPER_ADMIN only (via the matrix) - same pricing-decision bar as
    # calling-rates above.
    return upsert_number_rate(
        db, country=payload.country, number_type=payload.number_type,
        recurring_price_cents=payload.recurring_price_cents, currency=payload.currency,
        is_placeholder=payload.is_placeholder, actor=staff.id,
    )


@router.get("/ai-usage-rate", response_model=AIUsageRateResponse | None)
def get_ai_usage_rate_route(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return get_ai_usage_rate(db)


@router.put("/ai-usage-rate", response_model=AIUsageRateResponse)
def upsert_ai_usage_rate_route(
    payload: UpsertAIUsageRateRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("billing.manage_ai_usage_rates")),
):
    return upsert_ai_usage_rate(
        db, overage_price_cents_per_minute=payload.overage_price_cents_per_minute,
        currency=payload.currency, is_placeholder=payload.is_placeholder, actor=staff.id,
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
    staff: PlatformStaff = Depends(require_capability("numbers.manage_country_list")),
):
    # SUPER_ADMIN only - expanding the launch country list is a compliance/
    # commercial decision, same bar as a calling-rate change above.
    return upsert_supported_country(
        db, code=payload.code, name=payload.name, sort_order=payload.sort_order,
        emergency_calling_supported=payload.emergency_calling_supported, actor=staff.id,
    )


@router.delete("/countries/{code}", status_code=status.HTTP_204_NO_CONTENT)
def remove_supported_country_route(
    code: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_country_list")),
):
    remove_supported_country(db, code, actor=staff.id)
    return None


@router.put("/countries/{code}/market-status", response_model=SupportedCountryResponse)
def set_market_activation_status_route(
    code: str,
    payload: SetMarketActivationStatusRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_country_list")),
):
    """Production Readiness Standard doc §6.2 "Market Activation Registry" -
    same SUPER_ADMIN bar as the country list itself (PUT /countries above),
    since moving a country between CLOSED/INTERNAL_TEST/CONTROLLED_BETA/
    PAID_OPEN/SUSPENDED is exactly the "Rule of Authority" kind of call
    Engineering doesn't get to self-ratify."""
    try:
        market_status = MarketActivationStatus(payload.status)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e

    try:
        return set_market_activation_status(
            db, code, status=market_status, actor=staff.id, reason=payload.reason,
            legal_signoff_reference=payload.legal_signoff_reference, legal_signoff_by=payload.legal_signoff_by,
        )
    except UnsupportedCountryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except MissingLegalSignoffError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.put("/countries/{code}/registry-fields", response_model=SupportedCountryResponse)
def update_country_registry_fields_route(
    code: str,
    payload: UpdateCountryRegistryFieldsRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_country_list")),
):
    """Commercial Billing Operating Standard doc §34 registry dimensions -
    same SUPER_ADMIN bar as every other registry mutation on this table."""
    try:
        return update_country_registry_fields(
            db, code,
            customer_type_restrictions=payload.customer_type_restrictions,
            porting_supported=payload.porting_supported,
            recording_consent_basis=payload.recording_consent_basis,
            payments_enabled=payload.payments_enabled,
            marketing_claims_approved=payload.marketing_claims_approved,
            actor=staff.id,
        )
    except UnsupportedCountryError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/number-eligibility-rules", response_model=list[NumberEligibilityRuleResponse])
def list_number_eligibility_rules_route(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return list_number_eligibility_rules(db)


@router.put("/number-eligibility-rules", response_model=NumberEligibilityRuleResponse)
def upsert_number_eligibility_rule_route(
    payload: UpsertNumberEligibilityRuleRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_eligibility_rules")),
):
    # SUPER_ADMIN only - deciding a market/number-type needs an eligibility
    # case at all is a compliance/commercial decision, same bar as the
    # country list and calling-rate changes above.
    return upsert_number_eligibility_rule(
        db, country=payload.country, number_type=payload.number_type,
        required_evidence=payload.required_evidence, is_active=payload.is_active,
        emergency_calling_supported=payload.emergency_calling_supported,
        recording_supported=payload.recording_supported,
        allowed_calling_directions=payload.allowed_calling_directions, actor=staff.id,
    )


@router.delete("/number-eligibility-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_number_eligibility_rule_route(
    rule_id: str,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_eligibility_rules")),
):
    remove_number_eligibility_rule(db, rule_id, actor=staff.id)


@router.post("/number-eligibility-rules/seed-market-registry", response_model=list[NumberEligibilityRuleResponse])
def seed_market_release_registry_route(
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("numbers.manage_eligibility_rules")),
):
    """Commercial Billing Operating Standard P0-2 - seeds a market/release
    registry row for every currently-supported country's 'local' numbers.
    Idempotent, safe to re-run after a new country is added to the
    supported list."""
    return seed_market_release_registry(db, actor=staff.id)


@router.get("/number-eligibility-cases", response_model=list[NumberEligibilityCaseResponse])
def list_number_eligibility_cases_route(
    case_status: NumberEligibilityCaseStatus | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Review queue, diagnostic for any staff role to view (same posture as
    the risk-case queue) - approving/rejecting is the sensitive action,
    gated below."""
    return list_all_eligibility_cases(db, case_status)


@router.post("/number-eligibility-cases/{case_id}/approve", response_model=NumberEligibilityCaseResponse)
def approve_number_eligibility_case_route(
    case_id: str,
    payload: ResolveNumberEligibilityCaseRequest = ResolveNumberEligibilityCaseRequest(),
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("compliance.review_case")),
):
    try:
        return approve_number_eligibility_case(db, case_id, actor=staff.id, notes=payload.notes)
    except NumberEligibilityCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/number-eligibility-cases/{case_id}/reject", response_model=NumberEligibilityCaseResponse)
def reject_number_eligibility_case_route(
    case_id: str,
    payload: ResolveNumberEligibilityCaseRequest = ResolveNumberEligibilityCaseRequest(),
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("compliance.review_case")),
):
    try:
        return reject_number_eligibility_case(db, case_id, actor=staff.id, notes=payload.notes)
    except NumberEligibilityCaseNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


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
