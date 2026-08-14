from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing import service as billing_service
from app.compliance.service import has_approved_case, is_requirement_active
from app.consent.models import ConsentType
from app.consent.service import has_active_consent
from app.core.config import settings
from app.events.service import publish_number_activated, publish_number_reserved, publish_number_suspended
from app.integrations.billing import stripe_checkout
from app.integrations.telecom import twilio as telecom
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active
from app.notifications.service import (
    notify_number_activated,
    notify_number_assigned,
    notify_number_order_not_approved,
    notify_number_released,
    notify_number_suspended,
    notify_number_unassigned,
    notify_number_verification_required,
)
from app.numbering.identity.models import Account, AccountBillingClassification, AccountType, User, UserRole
from app.numbering.numbers.models import (
    IVROption,
    MarketActivationState,
    NumberEligibilityCase,
    NumberEligibilityCaseStatus,
    NumberEligibilityRule,
    PhoneNumber,
    PhoneNumberStatus,
    RingGroupDestination,
    SupportedCountry,
)

RESERVATION_TTL_MINUTES = 12
QUARANTINE_DAYS = 90
RENEWAL_PERIOD_DAYS = 30

# Real Stripe Checkout price for a number purchase (test mode - no real
# money moves yet). One flat price for every number regardless of country
# or type, a deliberate first-pass simplification - same "placeholder, not
# a real rate card" posture as app.usage.models.CallingRate's per-country
# calling prices. Revisit once real per-market pricing is decided.
NUMBER_PURCHASE_PRICE_CENTS = 100
NUMBER_PURCHASE_CURRENCY = "usd"


class NumberConflictError(Exception):
    """Raised when a number can't be reserved/purchased because another
    account already holds it, or the caller's own reservation lapsed."""


class ReservationExpiredError(NumberConflictError):
    """Raised specifically when the account's OWN reservation on this
    number lapsed (RESERVATION_TTL_MINUTES) - a subclass of
    NumberConflictError so anything already catching that broadly still
    catches this, but distinct enough that complete_number_purchase_from_
    checkout can tell it apart from "already fulfilled/duplicate webhook."
    Confirmed live (Commercial Billing Operating Standard acceptance test,
    2026-08-13): before this existed, a real Stripe payment that completed
    after the reservation had already expired was silently kept with no
    number delivered and no refund issued, because this case raised the
    same NumberConflictError as the harmless "already fulfilled" case and
    complete_number_purchase_from_checkout couldn't distinguish them."""


class NonCommercialAccountError(Exception):
    """Raised when an account whose billing_classification forbids live
    customer charges (Commercial Billing Operating Standard doc's Table 8
    + "COM-03: non-commercial classes cannot create live customer
    charges") tries to buy a number through the real Stripe payment path.
    Only COMMERCIAL_STANDALONE accounts may - every other class is either
    billed a different way (bundled/partner/legacy) or must never be
    billed at all (internal/demo/sandbox/QA/pilot)."""


def _assert_commercial_account(db: Session, account_id: str) -> None:
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None or account.billing_classification != AccountBillingClassification.COMMERCIAL_STANDALONE:
        classification = account.billing_classification.value if account else "unknown"
        raise NonCommercialAccountError(
            f"Account billing_classification {classification!r} cannot create a live charge"
        )


class TestAccountRestrictedError(Exception):
    """Raised when an account flagged is_test attempts a real-money action
    (Commercial Billing Operating Standard doc §14/§T) - see Account.
    is_test's docstring. Overlaps with NonCommercialAccountError above (see
    Account.is_test's merge-time docstring on the two fields) - both are
    checked, not consolidated, in this merge."""


class UnsupportedCountryError(Exception):
    """Raised for a country outside Zoiko Local's curated launch list (the
    SupportedCountry table) - narrower than Twilio's own coverage since a
    customer picking from an unreviewed 100+ country list would routinely
    hit countries with numbering, tax, or compliance requirements this
    platform hasn't reviewed yet."""


class InvalidAreaCodeError(Exception):
    """Raised for a non-numeric area code (e.g. a city name typed into the
    field instead of its numeric code) - confirmed live: without this,
    Twilio's own 400 for a bad AreaCode value ("chicao is not a valid
    integer: 'AreaCode'") passed straight through to the customer verbatim,
    vendor field name, error code and docs link included. Caught before
    ever calling Twilio, not just reworded after the fact."""


def list_supported_countries(db: Session) -> list[SupportedCountry]:
    return db.query(SupportedCountry).order_by(SupportedCountry.sort_order, SupportedCountry.code).all()


def upsert_supported_country(
    db: Session, *, code: str, name: str, sort_order: int = 0, emergency_calling_supported: bool = False
) -> SupportedCountry:
    """Staff-only, SUPER_ADMIN-gated at the route (see app.staff.routes) -
    expanding the launch country list is a compliance/commercial decision,
    the same bar as a calling-rate change (app.usage.service.
    upsert_calling_rate). emergency_calling_supported defaults False -
    setting it True is a Legal/Compliance claim that real E911 evidence
    exists for this country (Commercial Billing Operating Standard doc
    §10), not an engineering default to flip casually."""
    country = db.query(SupportedCountry).filter(SupportedCountry.code == code).first()
    if country is None:
        country = SupportedCountry(
            code=code, name=name, sort_order=sort_order,
            emergency_calling_supported=emergency_calling_supported,
        )
        db.add(country)
    else:
        country.name = name
        country.sort_order = sort_order
        country.emergency_calling_supported = emergency_calling_supported
    db.commit()
    db.refresh(country)
    return country


def remove_supported_country(db: Session, code: str) -> None:
    db.query(SupportedCountry).filter(SupportedCountry.code == code).delete()
    db.commit()


def _assert_supported_country(db: Session, country: str) -> None:
    """Market Activation Registry enforcement (Production Readiness &
    Go-Live Decision Standard §6.2) - blocks the two states the doc is
    unambiguous about (CLOSED: never activated; SUSPENDED: "new sales/
    provisioning blocked immediately"). INTERNAL_TEST/CONTROLLED_BETA/
    PAID_OPEN all pass this check today - see SupportedCountry's
    docstring for why those three aren't behaviorally distinguished yet."""
    row = db.query(SupportedCountry).filter(SupportedCountry.code == country).first()
    if row is None:
        raise UnsupportedCountryError(f"{country!r} is not on Zoiko Local's supported country list yet")
    if row.activation_state in (MarketActivationState.CLOSED, MarketActivationState.SUSPENDED):
        raise UnsupportedCountryError(
            f"{country!r} is {row.activation_state.value!r} in the Market Activation Registry - "
            f"not available for number purchase right now"
        )


def set_market_activation_state(
    db: Session, code: str, state: MarketActivationState, *, actor: str, notes: str | None = None,
) -> SupportedCountry:
    """Staff-only (numbers.manage_country_list - same bar as adding a
    country to the list at all). Deliberately does NOT attempt to enforce
    Table 6.3's "minimum market file before PAID_OPEN" checklist (legal
    entity, telecom authorization, tax registration, etc.) in code - this
    codebase has no real content for any of those items yet, and a
    software gate that can't actually verify a legal/tax/telecom
    determination would just be theater. This function trusts the actor
    (Legal/Tax/Compliance per the doc's ownership table) to only move a
    country to PAID_OPEN once that real-world review has actually
    happened; `notes` is where they record what it was."""
    country = db.query(SupportedCountry).filter(SupportedCountry.code == code).first()
    if country is None:
        raise UnsupportedCountryError(f"{code!r} is not on Zoiko Local's supported country list yet")
    previous_state = country.activation_state
    country.activation_state = state
    country.activation_notes = notes
    country.activation_changed_by = actor
    country.activation_changed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(country)
    log_event(
        db, actor=actor, action="numbers.market_activation_state_changed", target=f"supported_country:{code}",
        before={"activation_state": previous_state.value}, after={"activation_state": state.value, "notes": notes},
    )
    return country


class ComplianceRequiredError(Exception):
    """Raised when the number's country has an active KYC/KYB rule and the
    account has no approved compliance case covering it yet — the docs'
    "Compliance Pending" lifecycle state, enforced at the point of purchase."""


class EmergencyDisclosureRequiredError(Exception):
    """Raised when the account hasn't acknowledged the emergency-calling
    limitation disclosure yet - required before ANY number purchase, every
    country, no exceptions (unlike the KYC gate, which is country-specific).
    Roadmap doctrine: "Zoiko Local is not... an emergency-service operator" -
    this is the disclosure/acknowledgment Phase 1 actually calls for, not
    real E911 routing."""


class NumberEligibilityRequiredError(Exception):
    """Raised when the requested number's (country, number_type) has an
    active NumberEligibilityRule and no approved NumberEligibilityCase
    covers this specific number yet - the doc's eligibility_case gate,
    distinct from the account-level KYC ComplianceRequiredError above."""


class NumberEligibilityCaseNotFoundError(Exception):
    """Raised when a case id doesn't exist."""


def get_active_eligibility_rule(db: Session, country: str, number_type: str) -> NumberEligibilityRule | None:
    return (
        db.query(NumberEligibilityRule)
        .filter(
            NumberEligibilityRule.country == country.upper(),
            NumberEligibilityRule.number_type == number_type,
            NumberEligibilityRule.is_active.is_(True),
        )
        .first()
    )


def list_number_eligibility_rules(db: Session) -> list[NumberEligibilityRule]:
    return db.query(NumberEligibilityRule).order_by(NumberEligibilityRule.country, NumberEligibilityRule.number_type).all()


def upsert_number_eligibility_rule(
    db: Session, *, country: str, number_type: str, required_evidence: list[str], is_active: bool,
    emergency_calling_supported: bool = False, recording_supported: bool = True,
    allowed_calling_directions: str = "both",
) -> NumberEligibilityRule:
    """Staff-only, SUPER_ADMIN-gated at the route - same bar as the country
    list and calling-rate changes (this decides which numbers can even be
    purchased at all, not a routine support action). The three
    market/release registry fields default to what this product actually
    supports today (see NumberEligibilityRule's docstring) rather than an
    unreviewed per-country legal claim."""
    country = country.upper()
    rule = (
        db.query(NumberEligibilityRule)
        .filter(NumberEligibilityRule.country == country, NumberEligibilityRule.number_type == number_type)
        .first()
    )
    if rule is None:
        rule = NumberEligibilityRule(country=country, number_type=number_type)
        db.add(rule)
    rule.required_evidence = required_evidence
    rule.is_active = is_active
    rule.emergency_calling_supported = emergency_calling_supported
    rule.recording_supported = recording_supported
    rule.allowed_calling_directions = allowed_calling_directions
    db.commit()
    db.refresh(rule)
    return rule


def remove_number_eligibility_rule(db: Session, rule_id: str) -> None:
    db.query(NumberEligibilityRule).filter(NumberEligibilityRule.id == rule_id).delete()
    db.commit()


def seed_market_release_registry(db: Session) -> list[NumberEligibilityRule]:
    """Commercial Billing Operating Standard P0-2: "Implement a versioned
    market/release registry for every country and number type." Creates
    one row per currently-supported country for 'local' numbers (the only
    number_type this product actually sells today) that doesn't already
    have one - is_active=False, confirmed live against purchase_number:
    the eligibility-case gate triggers on the mere EXISTENCE of an ACTIVE
    rule, independent of required_evidence content (see
    NumberEligibilityRule's docstring), so seeding with is_active=True
    would have silently required every future number purchase in every
    market to go through a case-approval workflow nobody asked for - a
    real regression, caught by this codebase's own test suite. This seed
    is pure reference data until staff deliberately configure a market
    for real (via PUT .../number-eligibility-rules).

    Never touches a country that already has a row, active or not -
    re-running this after staff has configured real required_evidence or
    flipped is_active must not overwrite that decision back to defaults."""
    countries = list_supported_countries(db)
    existing = {
        (rule.country, rule.number_type) for rule in list_number_eligibility_rules(db)
    }
    created = []
    for country in countries:
        if (country.code, "local") in existing:
            continue
        created.append(
            upsert_number_eligibility_rule(
                db, country=country.code, number_type="local", required_evidence=[], is_active=False,
                emergency_calling_supported=False, recording_supported=True, allowed_calling_directions="both",
            )
        )
    return created


def _get_or_open_eligibility_case(db: Session, *, number: PhoneNumber, actor: str) -> NumberEligibilityCase:
    """One case per phone_number_id (the doc's "links requested number/
    market to evidence" - a case is scoped to THIS number request, not the
    account). Reopening after REJECTED/EXPIRED isn't automatic - a rejected
    case stays visible as the record of that decision; the customer submits
    fresh evidence via submit_number_eligibility_evidence instead of a new
    case silently appearing."""
    case = db.query(NumberEligibilityCase).filter(NumberEligibilityCase.phone_number_id == number.id).first()
    if case is not None:
        return case

    case = NumberEligibilityCase(
        phone_number_id=number.id, account_id=number.account_id,
        country=number.country, number_type=number.number_type,
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_case_opened",
        target=f"number_eligibility_case:{case.id}",
        after={"phone_number_id": number.id, "country": number.country, "number_type": number.number_type},
    )
    return case


def has_approved_eligibility_case(db: Session, phone_number_id: str) -> bool:
    return (
        db.query(NumberEligibilityCase)
        .filter(
            NumberEligibilityCase.phone_number_id == phone_number_id,
            NumberEligibilityCase.status == NumberEligibilityCaseStatus.APPROVED,
        )
        .first()
        is not None
    )


def list_eligibility_cases_for_account(db: Session, account_id: str) -> list[NumberEligibilityCase]:
    return (
        db.query(NumberEligibilityCase)
        .filter(NumberEligibilityCase.account_id == account_id)
        .order_by(NumberEligibilityCase.created_at.desc())
        .all()
    )


def list_all_eligibility_cases(db: Session, status: NumberEligibilityCaseStatus | None = None) -> list[NumberEligibilityCase]:
    query = db.query(NumberEligibilityCase)
    if status is not None:
        query = query.filter(NumberEligibilityCase.status == status)
    return query.order_by(NumberEligibilityCase.created_at.desc()).all()


def _get_eligibility_case(db: Session, case_id: str) -> NumberEligibilityCase:
    case = db.query(NumberEligibilityCase).filter(NumberEligibilityCase.id == case_id).first()
    if case is None:
        raise NumberEligibilityCaseNotFoundError(f"No eligibility case with id {case_id}")
    return case


def submit_number_eligibility_evidence(
    db: Session, case_id: str, evidence: list[dict], *, account_id: str, actor: str
) -> NumberEligibilityCase:
    """Customer-facing - appends evidence and, if the case was previously
    REJECTED, moves it back to PENDING for a fresh review (same retry
    pattern as compliance.service.start_kyc_verification's REJECTED case).
    APPROVED cases are left untouched - resubmitting evidence after
    approval would be pointless and risks a stale review overwriting a
    correct decision. account_id is checked here, before any mutation, so
    a case owned by another account is rejected atomically rather than
    the caller having to check ownership only after the write already
    committed."""
    case = _get_eligibility_case(db, case_id)
    if case.account_id != account_id:
        raise NumberEligibilityCaseNotFoundError(f"No eligibility case with id {case_id}")
    case.evidence = [*case.evidence, *evidence]
    if case.status == NumberEligibilityCaseStatus.REJECTED:
        case.status = NumberEligibilityCaseStatus.PENDING
        case.review_notes = None
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_evidence_submitted",
        target=f"number_eligibility_case:{case.id}", after={"evidence_count": len(case.evidence)},
    )
    return case


def approve_number_eligibility_case(db: Session, case_id: str, *, actor: str, notes: str | None = None) -> NumberEligibilityCase:
    case = _get_eligibility_case(db, case_id)
    before_status = case.status
    case.status = NumberEligibilityCaseStatus.APPROVED
    case.review_notes = notes
    case.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_case_approved",
        target=f"number_eligibility_case:{case.id}", before={"status": before_status}, after={"status": case.status},
    )
    return case


def reject_number_eligibility_case(db: Session, case_id: str, *, actor: str, notes: str | None = None) -> NumberEligibilityCase:
    case = _get_eligibility_case(db, case_id)
    before_status = case.status
    case.status = NumberEligibilityCaseStatus.REJECTED
    case.review_notes = notes
    case.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_case_rejected",
        target=f"number_eligibility_case:{case.id}", before={"status": before_status}, after={"status": case.status},
    )
    return case


def _kyc_requirement_type(db: Session, account_id: str) -> str:
    account = db.query(Account).filter(Account.id == account_id).first()
    return "kyc_individual" if account.account_type == AccountType.INDIVIDUAL else "kyc_business"


# Phase 3 "SMS by regulated market" - SMS business messaging is gated by the
# same country-rule/case engine as KYC (compliance/service.py), not a bare
# checkbox: e.g. US A2P 10DLC brand/campaign registration, or another
# market's sender-ID rules. Where a rule is data-defined for a country, the
# account needs an approved case before SMS can be turned on for a number
# there - same "rules stored as data, never hardcoded if-statements" pattern
# the docs require. WhatsApp has no equivalent gate here: its approval is
# Meta's own out-of-band process, which this system can't request or grant.
SMS_REQUIREMENT_TYPE = "sms_business_messaging"


def search_numbers(db: Session, country: str, number_type: str = "local", area_code: str | None = None) -> list[dict]:
    _assert_supported_country(db, country)
    if area_code is not None and area_code.strip() and not area_code.strip().isdigit():
        raise InvalidAreaCodeError(
            f"{area_code!r} isn't a valid area code - enter digits only, e.g. 312 for Chicago."
        )
    return telecom.search_available_numbers(country, number_type=number_type, area_code=area_code.strip() if area_code else area_code)


def reserve_number(db: Session, account_id: str, e164: str, country: str, number_type: str = "local") -> PhoneNumber:
    """Atomicity law: two accounts must never hold a live reservation on the
    same number. `SELECT ... FOR UPDATE` serializes concurrent reservers of an
    existing row; the unique constraint on `e164` catches the race where two
    requests both try to INSERT a brand-new row for the same number.
    """
    _assert_supported_country(db, country)
    now = datetime.now(timezone.utc)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()

    if number is None:
        number = PhoneNumber(
            e164=e164,
            country=country,
            number_type=number_type,
            status=PhoneNumberStatus.RESERVED,
            account_id=account_id,
            reserved_until=now + timedelta(minutes=RESERVATION_TTL_MINUTES),
        )
        db.add(number)
    elif number.status == PhoneNumberStatus.CANCELLED and number.cancelled_at is not None and (
        number.cancelled_at > now - timedelta(days=QUARANTINE_DAYS)
    ):
        raise NumberConflictError(
            f"{e164} was recently cancelled and is in a {QUARANTINE_DAYS}-day quarantine period"
        )
    elif number.status == PhoneNumberStatus.RESERVED and number.account_id != account_id and (
        number.reserved_until is not None and number.reserved_until > now
    ):
        raise NumberConflictError(f"{e164} is already reserved by another account")
    elif number.status in (
        PhoneNumberStatus.COMPLIANCE_PENDING,
        PhoneNumberStatus.PURCHASE_PENDING,
        PhoneNumberStatus.PROVISIONING,
        PhoneNumberStatus.ACTIVE,
        PhoneNumberStatus.SUSPENDED,
    ):
        raise NumberConflictError(f"{e164} is not available")
    else:
        # own expired-or-active reservation, or a released/cancelled row past quarantine: re-reserve it
        number.status = PhoneNumberStatus.RESERVED
        number.account_id = account_id
        number.number_type = number_type
        number.reserved_until = now + timedelta(minutes=RESERVATION_TTL_MINUTES)

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise NumberConflictError(f"{e164} was just reserved by another account") from e

    db.refresh(number)
    log_event(
        db,
        actor_id=account_id,
        action="number.reserved",
        target_type="phone_number",
        target_id=number.id,
        metadata={"e164": e164},
    )
    publish_number_reserved(account_id, number_id=number.id, e164=e164, country=country)
    return number


def _assert_purchase_eligible(db: Session, account_id: str, number: PhoneNumber, *, e164: str) -> None:
    """Commercial Billing Operating Standard doc's canonical transaction
    chain: "eligibility -> customer authorization -> service entitlement ->
    provider/provisioning action -> ... -> charge/tax/fee result" - eligibility
    comes FIRST, strictly before any charge. Also explicit at T1: "Live
    charge authorization requires billing_classification + billing_source +
    approved catalog/contract + active commercial account + market/service
    eligibility" - eligibility is a precondition FOR charge authorization,
    not something resolved afterward.

    Called from create_number_purchase_checkout_session BEFORE Stripe is
    ever involved (so a customer who needs KYC/eligibility documents is
    never charged while still waiting on them), and again from
    purchase_number itself as defense-in-depth against the case's status
    changing in the gap between checkout-session creation and the
    payment webhook actually firing."""
    # Architecture doc §5 "Subscription and Entitlement" - number allowance
    # gate. Checked before the (unwindable) emergency-disclosure/KYC checks
    # below since it's the cheapest possible reason to reject, and unlike
    # those, has nothing to persist on rejection.
    billing_service.assert_number_quota_available(db, account_id, exclude_number_id=number.id)
    # Graceful degradation (Architecture doc §9) - new number purchases
    # pause once a payment grace period expires; already-owned numbers are
    # never affected.
    billing_service.assert_billing_not_suspended(db, account_id)

    if not has_active_consent(db, account_id, ConsentType.EMERGENCY_CALLING_ACKNOWLEDGED):
        # Commercial Billing Operating Standard doc §10 - the disclosure
        # must reflect this specific number's market, not a single generic
        # sentence for every country (see SupportedCountry.
        # emergency_calling_supported's docstring). Still gates on the same
        # GLOBAL-or-jurisdiction consent check as before (has_active_consent
        # unchanged) - only the message text is country-aware.
        country = db.query(SupportedCountry).filter(SupportedCountry.code == number.country).first()
        if country is not None and country.emergency_calling_supported:
            capability_clause = "may not work reliably"
        else:
            capability_clause = f"is NOT currently supported in {number.country}"
        raise EmergencyDisclosureRequiredError(
            f"You must acknowledge that emergency (911/999) calling {capability_clause} through "
            "this service before purchasing a number — grant it via POST /compliance/consent first"
        )

    requirement_type = _kyc_requirement_type(db, account_id)
    if is_requirement_active(db, number.country, requirement_type) and not has_approved_case(
        db, account_id=account_id, jurisdiction=number.country, requirement_type=requirement_type
    ):
        # Doc's "Compliance Pending" lifecycle state - persisted, not just an
        # error, so the customer/admin can see this number is specifically
        # blocked on KYC/KYB rather than it looking abandoned in Reserved.
        # purchase_number can be retried from here once the case is approved.
        number.status = PhoneNumberStatus.COMPLIANCE_PENDING
        db.commit()
        log_event(
            db, actor_id=account_id, action="number.compliance_pending",
            target_type="phone_number", target_id=number.id,
            metadata={"e164": e164, "requirement_type": requirement_type},
        )
        owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
        if owner is not None:
            notify_number_verification_required(
                db, account_id=account_id, account_email=owner.email, e164=e164,
                action_summary=f"An approved {requirement_type} compliance case for {number.country}",
            )
        raise ComplianceRequiredError(
            f"An approved {requirement_type} compliance case for {number.country} "
            "is required before purchasing a number there"
        )

    if get_active_eligibility_rule(db, number.country, number.number_type) is not None and not has_approved_eligibility_case(
        db, number.id
    ):
        # Same persisted "Compliance Pending" lifecycle state as the KYC
        # gate above - purchase_number can be retried from here once the
        # eligibility case is approved. Reuses COMPLIANCE_PENDING rather
        # than adding a parallel status: both gates mean the same thing to
        # the customer ("purchase blocked pending a case being approved"),
        # just a different case type underneath.
        case = _get_or_open_eligibility_case(db, number=number, actor=account_id)
        if case.status != NumberEligibilityCaseStatus.APPROVED:
            number.status = PhoneNumberStatus.COMPLIANCE_PENDING
            db.commit()
            log_event(
                db, actor_id=account_id, action="number.eligibility_pending",
                target_type="phone_number", target_id=number.id,
                metadata={"e164": e164, "country": number.country, "number_type": number.number_type},
            )
            raise NumberEligibilityRequiredError(
                f"An approved market-eligibility case for {number.number_type} numbers in {number.country} "
                "is required before purchasing this number"
            )


def purchase_number(db: Session, account_id: str, e164: str) -> PhoneNumber:
    # Commercial Billing Operating Standard doc §32.1 - checked before
    # anything else so a tripped switch blocks new provisioning without
    # touching this number's existing reservation/eligibility state.
    assert_kill_switch_not_active(db, KillSwitchScope.NUMBER_PROVISIONING)
    # Deferred import - see reactivate_numbers_for_account_by_staff's
    # comment on why (app.risk.service imports this module already).
    # Production Readiness Standard Table 15's "Tenant" kill-switch scope.
    from app.risk.service import assert_account_kill_switch_not_active

    assert_account_kill_switch_not_active(db, account_id, KillSwitchScope.NUMBER_PROVISIONING)

    now = datetime.now(timezone.utc)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()

    if number is None or number.account_id != account_id or number.status not in (
        PhoneNumberStatus.RESERVED, PhoneNumberStatus.COMPLIANCE_PENDING,
    ):
        raise NumberConflictError(f"{e164} must be reserved by your account before purchase")
    if number.status == PhoneNumberStatus.RESERVED and (
        number.reserved_until is not None and number.reserved_until < now
    ):
        raise ReservationExpiredError(f"Reservation for {e164} expired — reserve it again before purchasing")

    _assert_purchase_eligible(db, account_id, number, e164=e164)

    number.status = PhoneNumberStatus.PURCHASE_PENDING
    number.provisioning_started_at = now
    db.commit()
    log_event(
        db, actor_id=account_id, action="number.purchase_pending",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )

    # Doc's "Provisioning" lifecycle state - "Provider activation in
    # progress," distinct from Purchase Pending's checkout/confirmation step
    # (no real billing gateway exists yet, so these happen back-to-back, but
    # the state is still real - visible if buy_number is slow, and a real
    # payment step can be inserted before this later without a model change).
    number.status = PhoneNumberStatus.PROVISIONING
    db.commit()
    log_event(
        db, actor_id=account_id, action="number.provisioning",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )

    try:
        bought = telecom.buy_number(e164)
    except telecom.TelecomError:
        # payment/provisioning failure must not strand the number silently —
        # release it back to Reserved so the customer can retry or it can expire
        number.status = PhoneNumberStatus.RESERVED
        number.provisioning_started_at = None
        db.commit()
        log_event(
            db, actor_id=account_id, action="number.purchase_failed",
            target_type="phone_number", target_id=number.id, metadata={"e164": e164},
        )
        owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
        if owner is not None:
            notify_number_order_not_approved(
                db, account_id=account_id, account_email=owner.email,
                order_reference=number.id, reason_category="provider unable to complete purchase",
            )
        raise

    number.status = PhoneNumberStatus.ACTIVE
    number.provider_sid = bought["sid"]
    number.reserved_until = None
    number.provisioning_started_at = None
    number.next_renewal_at = now + timedelta(days=RENEWAL_PERIOD_DAYS)
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=account_id, action="number.activated",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "provider_sid": bought["sid"]},
    )
    publish_number_activated(account_id, number_id=number.id, e164=e164)

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_number_activated(
            db, account_id=account_id, account_email=owner.email, e164=e164,
            organization_name=account.name if account else "your organization",
        )

    return number


def create_number_purchase_checkout_session(db: Session, account_id: str, e164: str) -> dict:
    """Real Stripe Checkout (test mode) for a number purchase.

    Commercial Billing Operating Standard doc's canonical transaction chain
    puts eligibility strictly BEFORE any charge ("eligibility -> customer
    authorization -> service entitlement -> ... -> charge/tax/fee result";
    T1: "Live charge authorization requires ... market/service eligibility")
    - _assert_purchase_eligible runs here, before Stripe is ever contacted,
    so a customer who still needs KYC/eligibility documents is never
    charged while waiting on them. purchase_number (called once Stripe
    confirms payment via the webhook - see complete_number_purchase_from_
    checkout below) re-checks the same gate as defense-in-depth against the
    case's status changing in the gap between session creation and webhook
    delivery, then performs the actual provisioning. Returns {id, url} -
    the customer is redirected to url.

    Global Plans, Pricing & Commercial Launch Standard doc: the account's
    first number is included with a paid plan, not charged. When
    billing_service.is_first_number_included says so, this skips Stripe
    entirely and calls purchase_number directly - same eligibility gate,
    same provisioning path, just no charge/redirect. See that function's
    docstring for why this doesn't yet implement the doc's recurring
    "$4.99/month" price for additional numbers."""
    _assert_commercial_account(db, account_id)
    # Commercial Billing Operating Standard doc §14/§T stopgap - see
    # Account.is_test's docstring. Overlaps with _assert_commercial_account
    # above (see TestAccountRestrictedError's docstring) - both are
    # checked, not consolidated, in this merge.
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is not None and account.is_test:
        raise TestAccountRestrictedError(f"Account {account_id} is flagged is_test and cannot create a live checkout session")

    now = datetime.now(timezone.utc)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != account_id or number.status not in (
        PhoneNumberStatus.RESERVED, PhoneNumberStatus.COMPLIANCE_PENDING,
    ):
        raise NumberConflictError(f"{e164} must be reserved by your account before checkout")
    if number.status == PhoneNumberStatus.RESERVED and (
        number.reserved_until is not None and number.reserved_until < now
    ):
        raise ReservationExpiredError(f"Reservation for {e164} expired — reserve it again before checkout")

    _assert_purchase_eligible(db, account_id, number, e164=e164)

    if billing_service.is_first_number_included(db, account_id, exclude_number_id=number.id):
        log_event(
            db, actor_id=account_id, action="number.included_purchase",
            target_type="phone_number", target_id=number.id, metadata={"e164": e164},
        )
        included_number = purchase_number(db, account_id, e164)
        return {"id": None, "url": None, "included": True, "number": included_number}

    session = stripe_checkout.create_checkout_session(
        e164=e164,
        amount_cents=NUMBER_PURCHASE_PRICE_CENTS,
        currency=NUMBER_PURCHASE_CURRENCY,
        success_url=f"{settings.frontend_base_url}/dashboard/numbers?checkout=success",
        cancel_url=f"{settings.frontend_base_url}/dashboard/numbers?checkout=cancelled",
        metadata={"e164": e164, "account_id": account_id},
    )
    log_event(
        db, actor_id=account_id, action="number.checkout_session_created",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "session_id": session["id"]},
    )
    return {"id": session["id"], "url": session["url"], "included": False, "number": None}


def complete_number_purchase_from_checkout(
    db: Session, *, e164: str, account_id: str, payment_intent_id: str | None = None
) -> PhoneNumber | None:
    """Called from the Stripe payment webhook once checkout.session.completed
    fires - runs the existing purchase_number flow with all its gates
    intact, rather than duplicating any of that logic. Returns None (not an
    error) for every known way purchase_number can fail to actually reach
    ACTIVE after a successful payment:

    - NumberConflictError (NOT the ReservationExpiredError subclass below):
      the number is no longer purchasable because it's already bought, or
      the webhook was retried after already succeeding once - idempotency
      against Stripe's at-least-once webhook delivery. Not refunded - this
      path means the number was already (or is being) fulfilled, or a
      duplicate delivery of an already-handled event.
    - ComplianceRequiredError / NumberEligibilityRequiredError:
      purchase_number already persisted the number into COMPLIANCE_PENDING
      and (for the KYC case) sent its own customer notification - correct
      behavior, nothing further for this handler to do. Not refunded - the
      customer still gets the number once the relevant case clears, this
      isn't a failure.
    - ReservationExpiredError / NumberQuotaExceededError /
      BillingSuspendedError / EmergencyDisclosureRequiredError /
      TelecomError: genuine post-payment fulfillment failures - the
      customer paid and won't be getting a number for it. Automatically
      refunded via Stripe (if payment_intent_id is available) so no real
      launch would leave a collected-but-unfulfilled payment sitting
      uncorrected. ReservationExpiredError was folded into the no-refund
      NumberConflictError bucket until a live acceptance test (2026-08-13)
      caught it silently keeping a real completed payment - see that
      exception's own docstring.
    """
    try:
        return purchase_number(db, account_id, e164)
    except ReservationExpiredError:
        if payment_intent_id:
            try:
                stripe_checkout.refund_payment(payment_intent_id)
            except stripe_checkout.PaymentError:
                pass
        return None
    except (NumberConflictError, ComplianceRequiredError, NumberEligibilityRequiredError):
        return None
    except (
        EmergencyDisclosureRequiredError,
        billing_service.NumberQuotaExceededError,
        billing_service.BillingSuspendedError,
        telecom.TelecomError,
    ):
        if payment_intent_id:
            try:
                stripe_checkout.refund_payment(payment_intent_id)
            except stripe_checkout.PaymentError:
                # Refund itself failed (e.g. provider outage) - logged by
                # trace_provider_call already; swallowed here so a refund
                # hiccup never turns into a 500 back to Stripe's webhook
                # (which would just cause pointless redelivery retries of
                # an event we've already fully handled on our side).
                pass
        return None


# purchase_number is entirely synchronous - it never returns to the caller
# with a number still sitting in PURCHASE_PENDING/PROVISIONING. A row
# observed in either state some time later means the process died mid-flight
# (crash, restart, OOM-kill) - a real, if rare, operational scenario the
# Architecture doc's "Provisioning Job... retry_count, error_code" concept is
# for. Threshold avoids a staff member racing a request that's still
# genuinely, legitimately in-flight this very second.
STUCK_PROVISIONING_THRESHOLD_MINUTES = 2


class NoStuckProvisioningError(Exception):
    """Raised when trying to recover a number that isn't actually stuck -
    either it's not in PURCHASE_PENDING/PROVISIONING at all, or it entered
    that state too recently to safely assume the original request died."""


def list_stuck_provisioning(db: Session) -> list[PhoneNumber]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROVISIONING_THRESHOLD_MINUTES)
    return (
        db.query(PhoneNumber)
        .filter(
            PhoneNumber.status.in_([PhoneNumberStatus.PURCHASE_PENDING, PhoneNumberStatus.PROVISIONING]),
            (PhoneNumber.provisioning_started_at.is_(None)) | (PhoneNumber.provisioning_started_at < cutoff),
        )
        .order_by(PhoneNumber.provisioning_started_at.asc().nulls_first())
        .all()
    )


def _assert_is_stuck(number: PhoneNumber | None, number_id: str) -> PhoneNumber:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_PROVISIONING_THRESHOLD_MINUTES)
    if (
        number is None
        or number.status not in (PhoneNumberStatus.PURCHASE_PENDING, PhoneNumberStatus.PROVISIONING)
        or (number.provisioning_started_at is not None and number.provisioning_started_at >= cutoff)
    ):
        raise NoStuckProvisioningError(f"{number_id} is not a stuck provisioning attempt")
    return number


def retry_provisioning(db: Session, staff_id: str, number_id: str) -> PhoneNumber:
    """Staff recovery action - re-attempts the provider purchase for a
    number stranded mid-flight. Reuses the exact same success/failure
    transitions as purchase_number's tail, just actor-attributed to staff."""
    number = db.query(PhoneNumber).filter(PhoneNumber.id == number_id).with_for_update().first()
    number = _assert_is_stuck(number, number_id)

    number.status = PhoneNumberStatus.PROVISIONING
    db.commit()
    log_event(
        db, actor_id=staff_id, action="number.provisioning_retried",
        target_type="phone_number", target_id=number.id, metadata={"e164": number.e164},
    )

    try:
        bought = telecom.buy_number(number.e164)
    except telecom.TelecomError:
        number.status = PhoneNumberStatus.RESERVED
        number.provisioning_started_at = None
        db.commit()
        log_event(
            db, actor_id=staff_id, action="number.purchase_failed",
            target_type="phone_number", target_id=number.id, metadata={"e164": number.e164, "retried_by_staff": True},
        )
        raise

    number.status = PhoneNumberStatus.ACTIVE
    number.provider_sid = bought["sid"]
    number.reserved_until = None
    number.provisioning_started_at = None
    number.next_renewal_at = datetime.now(timezone.utc) + timedelta(days=RENEWAL_PERIOD_DAYS)
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=staff_id, action="number.activated",
        target_type="phone_number", target_id=number.id,
        metadata={"e164": number.e164, "provider_sid": bought["sid"], "retried_by_staff": True},
    )

    owner = db.query(User).filter(User.account_id == number.account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == number.account_id).first()
        notify_number_activated(
            db, account_id=number.account_id, account_email=owner.email, e164=number.e164,
            organization_name=account.name if account else "your organization",
        )

    return number


def release_stuck_provisioning(db: Session, staff_id: str, number_id: str) -> PhoneNumber:
    """Unsticks a number without immediately retrying the provider purchase
    - e.g. staff suspects a provider-side outage and wants to investigate
    before hitting it again. Reverts to Reserved with a fresh TTL, same as a
    natural purchase failure, so the customer can retry themselves too."""
    number = db.query(PhoneNumber).filter(PhoneNumber.id == number_id).with_for_update().first()
    number = _assert_is_stuck(number, number_id)

    number.status = PhoneNumberStatus.RESERVED
    number.reserved_until = datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_TTL_MINUTES)
    number.provisioning_started_at = None
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=staff_id, action="number.provisioning_released",
        target_type="phone_number", target_id=number.id, metadata={"e164": number.e164},
    )
    return number


class NotDueForRenewalError(Exception):
    """Raised when trying to mark a number renewed that isn't ACTIVE or
    doesn't have a next_renewal_at in the past."""


# Commercial Billing Operating Standard doc §D2/§NUM-01 - "billing meter
# excludes FAILED/REJECTED/CANCELED... failed provisioning cannot produce
# active recurring rental." This codebase has no FAILED/REJECTED
# PhoneNumberStatus (a failed purchase reverts the row to RESERVED, which
# is already excluded here) - ACTIVE is the only billable state today.
# Centralized as a set (rather than an inline `== ACTIVE` check duplicated
# at every call site) so it stays usable in a SQL .in_() filter AND as a
# single, auditable, unit-testable point of truth as the state machine
# evolves - see is_number_billable.
BILLABLE_NUMBER_STATUSES = {PhoneNumberStatus.ACTIVE}


def is_number_billable(status: PhoneNumberStatus) -> bool:
    return status in BILLABLE_NUMBER_STATUSES


def list_due_renewals(db: Session) -> list[PhoneNumber]:
    """Numbers whose lifecycle renewal date has passed. There's no real
    payment gateway to charge yet (same gap purchase_number's docstring
    flags), so this is a staff-visible worklist, not an automated billing
    run - see mark_number_renewed."""
    now = datetime.now(timezone.utc)
    return (
        db.query(PhoneNumber)
        .filter(
            PhoneNumber.status.in_(BILLABLE_NUMBER_STATUSES),
            PhoneNumber.next_renewal_at.isnot(None),
            PhoneNumber.next_renewal_at <= now,
        )
        .order_by(PhoneNumber.next_renewal_at.asc())
        .all()
    )


def mark_number_renewed(db: Session, staff_id: str, number_id: str) -> PhoneNumber:
    """Staff bookkeeping action - advances a number's renewal date by one
    period. Deliberately does not touch billing/suspension: existing number
    ownership is explicitly exempt from billing-suspension effects
    (app.billing.service.assert_billing_not_suspended's docstring), and
    there's no real per-number payment step to fail against yet, so this
    must not invent a punitive failure mode that isn't backed by one."""
    number = db.query(PhoneNumber).filter(PhoneNumber.id == number_id).with_for_update().first()
    now = datetime.now(timezone.utc)
    if number is None or not is_number_billable(number.status) or (
        number.next_renewal_at is None or number.next_renewal_at > now
    ):
        raise NotDueForRenewalError(f"{number_id} is not currently due for renewal")

    number.next_renewal_at = now + timedelta(days=RENEWAL_PERIOD_DAYS)
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=staff_id, action="number.renewed",
        target_type="phone_number", target_id=number.id,
        metadata={"e164": number.e164, "next_renewal_at": number.next_renewal_at.isoformat()},
    )
    return number


def suspend_number(db: Session, user: User, e164: str, reason: str | None = None) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    if number is None or number.account_id != user.account_id or number.status != PhoneNumberStatus.ACTIVE:
        raise NumberConflictError(f"{e164} must be an active number owned by your account to suspend")
    assert_number_access(number, user)

    number.status = PhoneNumberStatus.SUSPENDED
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=user.id, action="number.suspended",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "reason": reason},
    )
    publish_number_suspended(user.account_id, number_id=number.id, e164=e164, reason=reason)

    owner = db.query(User).filter(User.account_id == user.account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_number_suspended(
            db, account_id=user.account_id, account_email=owner.email, e164=e164, reason=reason,
            account_phone=owner.phone_number,
        )

    return number


def suspend_numbers_for_account_by_system(db: Session, account_id: str, *, reason: str) -> list[PhoneNumber]:
    """System-initiated counterpart to suspend_number, with no User in the
    loop - used by the risk engine's auto-suspend workflow (Roadmap doc §13
    Risk Register: "rapid suspension workflow"), which acts across accounts
    on its own trigger rather than a customer/staff request. Suspends every
    ACTIVE number on the account; already-suspended/cancelled numbers are
    left alone."""
    numbers = (
        db.query(PhoneNumber)
        .filter(PhoneNumber.account_id == account_id, PhoneNumber.status == PhoneNumberStatus.ACTIVE)
        .with_for_update()
        .all()
    )
    for number in numbers:
        number.status = PhoneNumberStatus.SUSPENDED
    db.commit()

    for number in numbers:
        db.refresh(number)
        log_event(
            db, actor="system:risk_engine", action="number.suspended",
            target=f"phone_number:{number.id}", reason=reason, after={"e164": number.e164},
        )
        publish_number_suspended(account_id, number_id=number.id, e164=number.e164, reason=reason)

    if numbers:
        owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
        if owner is not None:
            for number in numbers:
                notify_number_suspended(
                    db, account_id=account_id, account_email=owner.email, e164=number.e164, reason=reason,
                    account_phone=owner.phone_number,
                )

    return numbers


def reactivate_numbers_for_account_by_staff(
    db: Session, account_id: str, *, staff_id: str, reason: str | None = None
) -> list[PhoneNumber]:
    """Staff-initiated reversal of a suspension - the review/reversal half
    of the risk engine's auto-suspend workflow (a false positive or a
    resolved dispute shouldn't need engineering intervention to undo).
    Reactivates every SUSPENDED number on the account; numbers already
    cancelled or never suspended are left alone."""
    numbers = (
        db.query(PhoneNumber)
        .filter(PhoneNumber.account_id == account_id, PhoneNumber.status == PhoneNumberStatus.SUSPENDED)
        .with_for_update()
        .all()
    )
    for number in numbers:
        number.status = PhoneNumberStatus.ACTIVE
    db.commit()

    # Deferred import - app.risk.service imports this module (for
    # suspend_numbers_for_account_by_system), so a module-level import here
    # would be circular. Explicit staff reinstate is the one place
    # risk_state is allowed to leave SUSPENDED_FRAUD (see
    # recompute_risk_state's docstring) - drops straight to the account's
    # current baseline tier rather than through REVIEW_REQUIRED, since a
    # staff member choosing to reinstate is a stronger signal than the
    # score-decay path alone.
    from app.risk.service import RiskState, _baseline_risk_state

    account_for_risk = db.query(Account).filter(Account.id == account_id).first()
    if account_for_risk is not None and account_for_risk.risk_state == RiskState.SUSPENDED_FRAUD:
        account_for_risk.risk_state = _baseline_risk_state(db, account_for_risk)
        db.commit()

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    account = db.query(Account).filter(Account.id == account_id).first()
    for number in numbers:
        db.refresh(number)
        log_event(
            db, actor_id=staff_id, action="number.reactivated",
            target_type="phone_number", target_id=number.id, reason=reason, metadata={"e164": number.e164},
        )
        publish_number_activated(account_id, number_id=number.id, e164=number.e164)
        if owner is not None:
            notify_number_activated(
                db, account_id=account_id, account_email=owner.email, e164=number.e164,
                organization_name=account.name if account else "your organization",
            )

    return numbers


def cancel_number(db: Session, user: User, e164: str) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    if number is None or number.account_id != user.account_id or number.status not in (
        PhoneNumberStatus.ACTIVE, PhoneNumberStatus.SUSPENDED,
    ):
        raise NumberConflictError(f"{e164} must be an active or suspended number owned by your account to cancel")
    assert_number_access(number, user)

    # Release on Twilio *before* marking cancelled locally - if this fails,
    # the number stays ACTIVE/SUSPENDED here too, so the customer isn't left
    # thinking it's cancelled while it's still live (and billing) for real.
    if number.provider_sid:
        telecom.release_number(number.provider_sid)

    number.status = PhoneNumberStatus.CANCELLED
    number.cancelled_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=user.id, action="number.cancelled",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )

    owner = db.query(User).filter(User.account_id == user.account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_number_released(db, account_id=user.account_id, account_email=owner.email, e164=e164)

    return number


def configure_routing(
    db: Session,
    user: User,
    e164: str,
    forwarding_number: str | None,
    business_hours_start: time | None,
    business_hours_end: time | None,
    business_hours_timezone: str,
    ai_receptionist_enabled: bool = False,
    escalation_user_id: str | None = None,
    whatsapp_enabled: bool = False,
    sms_enabled: bool = False,
) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")
    assert_number_access(number, user)

    if sms_enabled and not number.sms_enabled and is_requirement_active(
        db, number.country, SMS_REQUIREMENT_TYPE
    ) and not has_approved_case(
        db, account_id=user.account_id, jurisdiction=number.country, requirement_type=SMS_REQUIREMENT_TYPE
    ):
        raise ComplianceRequiredError(
            f"An approved {SMS_REQUIREMENT_TYPE} compliance case for {number.country} "
            "is required before enabling SMS on this number"
        )

    try:
        ZoneInfo(business_hours_timezone)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Unknown timezone: {business_hours_timezone}") from e

    if escalation_user_id is not None:
        nominee = db.query(User).filter(User.id == escalation_user_id, User.account_id == user.account_id).first()
        if nominee is None:
            raise NumberConflictError(f"No team member with id {escalation_user_id} on this account")

    number.forwarding_number = forwarding_number
    number.business_hours_start = business_hours_start
    number.business_hours_end = business_hours_end
    number.business_hours_timezone = business_hours_timezone
    number.ai_receptionist_enabled = ai_receptionist_enabled
    number.escalation_user_id = escalation_user_id
    number.whatsapp_enabled = whatsapp_enabled
    number.sms_enabled = sms_enabled
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=user.id, action="number.routing_configured",
        target_type="phone_number", target_id=number.id,
        metadata={
            "e164": e164,
            "forwarding_number": forwarding_number,
            "escalation_user_id": escalation_user_id,
        },
    )
    return number


MAX_RING_GROUP_SIZE = 5


class RingGroupTooLargeError(Exception):
    """Raised when trying to set more than MAX_RING_GROUP_SIZE destinations."""


def set_ring_group(db: Session, user: User, e164: str, destinations: list[str]) -> list[RingGroupDestination]:
    """Replaces the number's entire ring group in one call - simpler and
    less error-prone for a customer-facing "these are my destinations,
    in this order" form than incremental add/remove endpoints. An empty
    list clears the ring group entirely, reverting the number to plain
    single-forwarding_number behavior (see voice.py's incoming_call)."""
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")
    assert_number_access(number, user)

    if len(destinations) > MAX_RING_GROUP_SIZE:
        raise RingGroupTooLargeError(f"A ring group may have up to {MAX_RING_GROUP_SIZE} destinations")

    db.query(RingGroupDestination).filter(RingGroupDestination.phone_number_id == number.id).delete()
    rows = [
        RingGroupDestination(phone_number_id=number.id, destination_number=dest, ring_order=i)
        for i, dest in enumerate(destinations)
    ]
    db.add_all(rows)
    db.commit()

    log_event(
        db, actor_id=user.account_id, action="number.ring_group_updated",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "destinations": destinations},
    )
    return list_ring_group(db, e164)


def list_ring_group(db: Session, e164: str) -> list[RingGroupDestination]:
    return (
        db.query(RingGroupDestination)
        .join(PhoneNumber, PhoneNumber.id == RingGroupDestination.phone_number_id)
        .filter(PhoneNumber.e164 == e164)
        .order_by(RingGroupDestination.ring_order.asc())
        .all()
    )


MAX_IVR_OPTIONS = 10
_VALID_IVR_DIGITS = set("0123456789")


class InvalidIVROptionError(Exception):
    """Raised for a digit outside 0-9, a duplicate digit, or too many options."""


def set_ivr_menu(
    db: Session, user: User, e164: str, greeting: str, options: dict[str, str]
) -> tuple[PhoneNumber, list[IVROption]]:
    """Replaces the number's entire IVR menu in one call, same pattern as
    set_ring_group - simpler for a customer-facing "here's my whole menu"
    form than incremental per-digit endpoints. An empty greeting clears the
    menu entirely (see clear_ivr_menu), reverting to the number's existing
    ring-group/receptionist/voicemail behavior."""
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")
    assert_number_access(number, user)

    if not greeting.strip():
        raise InvalidIVROptionError("A greeting message is required")
    if len(options) > MAX_IVR_OPTIONS:
        raise InvalidIVROptionError(f"An IVR menu may have up to {MAX_IVR_OPTIONS} options")
    for digit in options:
        if digit not in _VALID_IVR_DIGITS:
            raise InvalidIVROptionError(f"{digit!r} is not a valid digit - use 0-9")

    number.ivr_greeting = greeting
    db.query(IVROption).filter(IVROption.phone_number_id == number.id).delete()
    rows = [
        IVROption(phone_number_id=number.id, digit=digit, destination_number=destination)
        for digit, destination in options.items()
    ]
    db.add_all(rows)
    db.commit()

    log_event(
        db, actor_id=user.account_id, action="number.ivr_menu_updated",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "options": options},
    )
    return get_ivr_menu(db, e164)


def get_ivr_menu(db: Session, e164: str) -> tuple[PhoneNumber | None, list[IVROption]]:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None:
        return None, []
    options = (
        db.query(IVROption)
        .filter(IVROption.phone_number_id == number.id)
        .order_by(IVROption.digit.asc())
        .all()
    )
    return number, options


def clear_ivr_menu(db: Session, user: User, e164: str) -> None:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")
    assert_number_access(number, user)

    number.ivr_greeting = None
    db.query(IVROption).filter(IVROption.phone_number_id == number.id).delete()
    db.commit()

    log_event(
        db, actor_id=user.account_id, action="number.ivr_menu_cleared",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )


def sync_webhook(db: Session, user: User, e164: str) -> PhoneNumber:
    """(Re)points this number's Twilio voice webhook at the current
    PUBLIC_BASE_URL - needed whenever that URL changes (e.g. a fresh ngrok
    tunnel in dev, since free-tier ngrok issues a new URL every restart and
    buy_number() only wires this up once, at purchase time)."""
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id or number.provider_sid is None:
        raise NumberConflictError(f"{e164} must be a purchased number owned by your account")
    assert_number_access(number, user)

    if not settings.public_base_url:
        raise NumberConflictError("PUBLIC_BASE_URL is not configured - nothing to point the webhook at")

    telecom.set_voice_webhook(number.provider_sid, settings.public_base_url)
    log_event(
        db, actor_id=user.id, action="number.webhook_synced",
        target_type="phone_number", target_id=number.id,
        metadata={"e164": e164, "public_base_url": settings.public_base_url},
    )
    return number


def list_account_numbers(db: Session, account_id: str, *, user: User) -> list[PhoneNumber]:
    """Owner/Admin see every number on the account. A plain Member only sees
    numbers assigned to them - unassigned numbers aren't theirs to manage
    yet, they haven't been handed anything."""
    query = db.query(PhoneNumber).filter(PhoneNumber.account_id == account_id)
    if user.role == UserRole.MEMBER:
        query = query.filter(PhoneNumber.assigned_user_id == user.id)
    return query.all()


def assigned_number_ids(db: Session, user: User) -> list[str] | None:
    """Number IDs a Member is scoped to across calls/voicemail/video/AI
    summaries - the same assignment boundary `assert_number_access` enforces
    for direct number management. Returns None for Owner/Admin, meaning
    "no restriction" rather than "empty list" (which would hide everything)."""
    if user.role != UserRole.MEMBER:
        return None
    rows = (
        db.query(PhoneNumber.id)
        .filter(PhoneNumber.account_id == user.account_id, PhoneNumber.assigned_user_id == user.id)
        .all()
    )
    return [r[0] for r in rows]


def assign_number(db: Session, *, account_id: str, e164: str, user_id: str | None, actor: str) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")

    if user_id is not None:
        assignee = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
        if assignee is None:
            raise NumberConflictError(f"No team member with id {user_id} on this account")

    before_assignee = number.assigned_user_id
    number.assigned_user_id = user_id
    db.commit()
    db.refresh(number)

    log_event(
        db, actor=actor, action="number.assigned",
        target=f"phone_number:{number.id}",
        before={"assigned_user_id": before_assignee},
        after={"assigned_user_id": user_id},
    )

    account = db.query(Account).filter(Account.id == account_id).first()
    organization_name = account.name if account else "your organization"
    if user_id is not None:
        notify_number_assigned(
            db, account_id=account_id, account_email=assignee.email, e164=e164, organization_name=organization_name,
        )
    if before_assignee is not None and before_assignee != user_id:
        previous_user = db.query(User).filter(User.id == before_assignee).first()
        if previous_user is not None:
            notify_number_unassigned(
                db, account_id=account_id, account_email=previous_user.email, e164=e164,
                previous_target=previous_user.email,
                lifecycle_status=number.status.value,
                route_summary=(
                    f"forward to {number.forwarding_number}" if number.forwarding_number else "no configured forwarding route"
                ),
            )
    return number


def assert_owns_number(db: Session, user: User, e164: str) -> PhoneNumber:
    """Looks up a number by e164 and confirms the caller's account owns
    it - the read-path counterpart to the account_id checks every
    write path already does inline. Used by GET routes (e.g. ring group)
    that would otherwise leak which destinations another account's
    number forwards to."""
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")
    return number


def assert_number_access(number: PhoneNumber, user: User) -> None:
    """Owner/Admin can manage any number on their account. A Member can only
    manage a number that's been assigned to them. Shared by every module
    (calls, voicemail, AI summaries, routing) that gates a per-number action
    on the same assignment boundary."""
    if user.role == UserRole.MEMBER and number.assigned_user_id != user.id:
        raise NumberConflictError(f"{number.e164} is not assigned to you")
