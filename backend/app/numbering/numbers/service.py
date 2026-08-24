import uuid
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
from app.events.service import (
    publish_number_activated,
    publish_number_reserved,
    publish_number_suspended,
)
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.storage import s3 as storage
from app.integrations.telecom import twilio as telecom
from app.ops.models import KillSwitchScope
from app.ops.service import assert_kill_switch_not_active
from app.usage import service as usage_service
from app.notifications.service import (
    notify_number_activated,
    notify_number_assigned,
    notify_number_order_not_approved,
    notify_number_released,
    notify_number_suspended,
    notify_number_unassigned,
    notify_number_verification_required,
    send_internal_alert,
)
from app.numbering.identity.models import Account, AccountBillingClassification, AccountType, User, UserRole
from app.numbering.numbers.models import (
    CallerIdentity,
    CallerIdentityStatus,
    IVROption,
    MarketActivationStatus,
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

# Fallback Stripe Checkout price for a number purchase (test mode - no
# real money moves yet), used only when usage_service.get_number_rate
# returns nothing at all (e.g. a number_type with no seeded fallback row) -
# create_number_purchase_checkout_session now prices from the real
# NumberRate table (country/number_type specific, doc-sourced $4.99
# baseline) first and only falls back to this flat placeholder as a last
# resort. Revisit once real per-market pricing is fully populated.
NUMBER_PURCHASE_PRICE_CENTS = 100
NUMBER_PURCHASE_CURRENCY = "usd"
# Global Plans, Pricing & Commercial Launch doc §5.1: "Apply an inclusion
# threshold; higher-cost or regulated numbers show a surcharge before
# checkout" - the doc requires this mechanism but gives no specific dollar
# figure for the threshold itself (unlike the $4.99 baseline, which IS
# doc-sourced). Set equal to the current NumberRate baseline as an honest
# placeholder (not a ratified business figure) - this means today's single
# seeded rate produces a $0 surcharge by construction, and the mechanism
# activates correctly the moment a real higher-cost country/type rate is
# added, without needing another code change.
NUMBER_INCLUSION_THRESHOLD_CENTS = 499


class NumberConflictError(Exception):
    """Raised when a number can't be reserved/purchased because another
    account already holds it, or the caller's own reservation lapsed."""


class ReservationExpiredError(NumberConflictError):
    """Raised specifically when the account's OWN reservation on this
    number lapsed (RESERVATION_TTL_MINUTES) - a subclass of
    NumberConflictError so anything already catching that broadly still
    catches this, but distinct enough for callers to give the customer a
    clearer "reserve it again" message than the generic conflict case."""


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


class MarketNotActivatedError(Exception):
    """Production Readiness Standard doc §6.2 "Market Activation Registry" -
    raised when a country is on the SupportedCountry list (so
    UnsupportedCountryError doesn't apply) but its market_status doesn't
    permit this caller to search/reserve/purchase there right now. CLOSED
    and SUSPENDED block everyone; INTERNAL_TEST and CONTROLLED_BETA block
    everyone except accounts flagged is_test - there's no invite-list
    model yet to distinguish "internal tester" from "invited beta
    customer", so both non-PAID_OPEN-but-active states share the same
    is_test gate for now (see _assert_market_activated's docstring)."""


class MissingLegalSignoffError(Exception):
    """Readiness doc §6.2: "PAID_OPEN only after legal/tax/telecom/privacy/
    consumer review and named sign-off." Raised by set_market_activation_
    status when a transition INTO PAID_OPEN is attempted without both
    legal_signoff_reference and legal_signoff_by - previously a free-text
    audit `reason` (e.g. "testing") was sufficient to open a market for
    real commercial sale, which is exactly the gap this doc calls out."""


_SUPPORTED_COUNTRIES_CACHE_KEY = "numbers:supported_countries"
# Moderate TTL, backed by explicit invalidation on every mutator below
# (upsert_supported_country/remove_supported_country/
# set_market_activation_status) - the TTL is just the safety net for a
# write path this list somehow isn't invalidated against, not the primary
# staleness control. Called on every /numbers/search page load.
_SUPPORTED_COUNTRIES_CACHE_TTL_SECONDS = 60


def _serialize_supported_country(country: SupportedCountry) -> dict:
    return {
        "id": country.id,
        "code": country.code,
        "name": country.name,
        "sort_order": country.sort_order,
        "emergency_calling_supported": country.emergency_calling_supported,
        "market_status": country.market_status.value,
        "created_at": country.created_at.isoformat() if country.created_at else None,
    }


def _deserialize_supported_country(data: dict) -> SupportedCountry:
    return SupportedCountry(
        id=data["id"],
        code=data["code"],
        name=data["name"],
        sort_order=data["sort_order"],
        emergency_calling_supported=data["emergency_calling_supported"],
        market_status=MarketActivationStatus(data["market_status"]),
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_supported_countries_cache() -> None:
    cache_delete(_SUPPORTED_COUNTRIES_CACHE_KEY)


def list_supported_countries(db: Session) -> list[SupportedCountry]:
    cached = cache_get(_SUPPORTED_COUNTRIES_CACHE_KEY)
    if cached is not None:
        return [_deserialize_supported_country(row) for row in cached]
    countries = db.query(SupportedCountry).order_by(SupportedCountry.sort_order, SupportedCountry.code).all()
    cache_set(
        _SUPPORTED_COUNTRIES_CACHE_KEY,
        [_serialize_supported_country(c) for c in countries],
        ttl_seconds=_SUPPORTED_COUNTRIES_CACHE_TTL_SECONDS,
    )
    return countries


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
    _invalidate_supported_countries_cache()
    return country


def update_country_registry_fields(
    db: Session, code: str, *, customer_type_restrictions: list[str] | None, porting_supported: bool,
    recording_consent_basis: str | None, payments_enabled: bool, marketing_claims_approved: bool, actor: str,
) -> SupportedCountry:
    """Commercial Billing Operating Standard doc §34 registry dimensions -
    separate from upsert_supported_country (which only ever covered basic
    country creation/enablement) since these represent a distinct real
    review per dimension, not a bundled property of adding a country to
    the list. Same SUPER_ADMIN bar as every other registry mutation on
    this table."""
    country = db.query(SupportedCountry).filter(SupportedCountry.code == code).first()
    if country is None:
        raise UnsupportedCountryError(f"{code!r} is not on Zoiko Local's supported country list yet")
    before = {
        "customer_type_restrictions": country.customer_type_restrictions,
        "porting_supported": country.porting_supported,
        "recording_consent_basis": country.recording_consent_basis,
        "payments_enabled": country.payments_enabled,
        "marketing_claims_approved": country.marketing_claims_approved,
    }
    country.customer_type_restrictions = customer_type_restrictions
    country.porting_supported = porting_supported
    country.recording_consent_basis = recording_consent_basis
    country.payments_enabled = payments_enabled
    country.marketing_claims_approved = marketing_claims_approved
    db.commit()
    db.refresh(country)
    _invalidate_supported_countries_cache()
    log_event(
        db, actor=actor, action="numbering.country_registry_fields_updated", target=f"supported_country:{code}",
        before=before, after={
            "customer_type_restrictions": customer_type_restrictions, "porting_supported": porting_supported,
            "recording_consent_basis": recording_consent_basis, "payments_enabled": payments_enabled,
            "marketing_claims_approved": marketing_claims_approved,
        },
    )
    return country


def remove_supported_country(db: Session, code: str) -> None:
    db.query(SupportedCountry).filter(SupportedCountry.code == code).delete()
    db.commit()
    _invalidate_supported_countries_cache()


def _assert_supported_country(db: Session, country: str) -> None:
    row = db.query(SupportedCountry).filter(SupportedCountry.code == country).first()
    if row is None:
        raise UnsupportedCountryError(f"{country!r} is not on Zoiko Local's supported country list yet")


def _assert_market_activated(db: Session, country: str, account_id: str) -> None:
    """Production Readiness Standard doc §6.2/Annex B - "Market availability
    is policy-controlled and default-deny." Separate from
    _assert_supported_country (which only asks "do we have a row for this
    country at all") - a country can be on the launch list yet still be
    CLOSED/SUSPENDED for everyone, or INTERNAL_TEST/CONTROLLED_BETA for
    testers only. Callers run _assert_supported_country first so a
    genuinely unknown country still gets that clearer error instead of
    this one."""
    supported = db.query(SupportedCountry).filter(SupportedCountry.code == country).first()
    if supported is None or supported.market_status == MarketActivationStatus.PAID_OPEN:
        return
    if supported.market_status in (MarketActivationStatus.INTERNAL_TEST, MarketActivationStatus.CONTROLLED_BETA):
        account = db.query(Account).filter(Account.id == account_id).first()
        if account is not None and account.is_test:
            return
        raise MarketNotActivatedError(
            f"{country!r} is not yet open for general commercial sale"
        )
    raise MarketNotActivatedError(f"{country!r} is not currently available")


def set_market_activation_status(
    db: Session, code: str, *, status: MarketActivationStatus, actor: str, reason: str,
    legal_signoff_reference: str | None = None, legal_signoff_by: str | None = None,
) -> SupportedCountry:
    """Staff-only, SUPER_ADMIN-gated at the route - moving a market between
    CLOSED/INTERNAL_TEST/CONTROLLED_BETA/PAID_OPEN/SUSPENDED is exactly the
    kind of commercial/legal decision this doc's Rule of Authority reserves
    from Engineering self-ratification; this function is the enforcement
    mechanism a human decision is recorded through, not the decision
    itself. `reason` is mandatory - see Annex B's "every override... has
    actor, reason, timestamp and evidence".

    Readiness doc §6.2: a transition INTO PAID_OPEN additionally requires
    legal_signoff_reference (a ticket/decision ID) and legal_signoff_by (the
    named reviewer) - `reason` alone used to be enough to open a market for
    real commercial sale, which is exactly the "provider has numbers there"
    shortcut this doc prohibits. Not required for any other transition
    (INTERNAL_TEST/CONTROLLED_BETA/SUSPENDED/CLOSED), which don't carry the
    same "customers can now actually pay for this" weight."""
    country = db.query(SupportedCountry).filter(SupportedCountry.code == code).first()
    if country is None:
        raise UnsupportedCountryError(f"{code!r} is not on Zoiko Local's supported country list yet")
    if status == MarketActivationStatus.PAID_OPEN and not (legal_signoff_reference and legal_signoff_by):
        raise MissingLegalSignoffError(
            f"Opening {code!r} for paid sale requires legal_signoff_reference and legal_signoff_by "
            f"(Readiness Standard doc §6.2 - a named legal/tax/telecom/privacy review, not just a reason string)"
        )
    previous_status = country.market_status
    country.market_status = status
    if status == MarketActivationStatus.PAID_OPEN:
        country.legal_signoff_reference = legal_signoff_reference
        country.legal_signoff_by = legal_signoff_by
    db.commit()
    db.refresh(country)
    _invalidate_supported_countries_cache()
    log_event(
        db, actor=actor, action="market.activation_status_changed",
        target=f"supported_country:{code}", reason=reason,
        before={"market_status": previous_status.value},
        after={
            "market_status": status.value,
            "legal_signoff_reference": legal_signoff_reference, "legal_signoff_by": legal_signoff_by,
        },
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


class NumberDocumentTypeUnsupportedError(Exception):
    """Same accepted-types posture as compliance.service.UnsupportedDocumentTypeError."""


class NumberDocumentTooLargeError(Exception):
    """Same size cap as compliance.service.DocumentTooLargeError."""


class NumberEligibilityDocumentRequiredError(Exception):
    """Raised when submit_number_eligibility_bundle is called before any
    document has been uploaded to the case - Twilio's bundle requires a
    real supporting document, there's nothing to submit without one."""


_ALLOWED_ELIGIBILITY_DOCUMENT_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_ELIGIBILITY_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024


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


def submit_number_eligibility_document(
    db: Session, case_id: str, *, account_id: str, document_type: str,
    filename: str, content_type: str, data: bytes, actor: str,
) -> NumberEligibilityCase:
    """Real gap closed 2026-08-22 - mirrors compliance.service.submit_document
    exactly (server-generated storage key, content-type/size validation,
    upload via the same S3 Provider Gateway) since this is the same kind of
    evidence, just scoped to a number-eligibility case instead of an
    account-level KYC case. Does NOT itself talk to Twilio - this only
    stores our own copy; submit_number_eligibility_bundle below uploads
    (a copy of) the most recent one to Twilio when the customer is ready
    to submit for review."""
    case = _get_eligibility_case(db, case_id)
    if case.account_id != account_id:
        raise NumberEligibilityCaseNotFoundError(f"No eligibility case with id {case_id}")
    if content_type not in _ALLOWED_ELIGIBILITY_DOCUMENT_CONTENT_TYPES:
        raise NumberDocumentTypeUnsupportedError(
            f"{content_type} is not an accepted document type - upload a PDF, JPEG, or PNG"
        )
    if len(data) > _MAX_ELIGIBILITY_DOCUMENT_SIZE_BYTES:
        raise NumberDocumentTooLargeError("Document exceeds the 10MB upload limit")

    storage_key = f"numbering-eligibility-documents/{case.id}/{uuid.uuid4()}-{filename}"
    storage.upload_object(storage_key, data, content_type)

    new_doc = {
        "document_type": document_type,
        "storage_key": storage_key,
        "filename": filename,
        "content_type": content_type,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    case.documents = [*case.documents, new_doc]  # reassign, not .append() - JSON columns need a new object to detect the change
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_document_submitted",
        target=f"number_eligibility_case:{case.id}", after={"document_type": document_type, "filename": filename},
    )
    return case


def submit_number_eligibility_bundle(
    db: Session, case_id: str, *, account_id: str, end_user_attributes: dict, end_user_type: str = "individual",
    actor: str,
) -> NumberEligibilityCase:
    """The real submission to Twilio's own compliance review - our
    `evidence`/`documents` fields alone were never enough, Twilio requires
    its own reviewed Regulatory Bundle before it will activate a
    restricted number type (confirmed live against Twilio's Regulations
    API for GB local/individual, 2026-08-22). Requires at least one
    document already uploaded via submit_number_eligibility_document.
    Real, no-mock - every Twilio call here is a genuine API call, same
    discipline as every other Provider Gateway integration this session."""
    case = _get_eligibility_case(db, case_id)
    if case.account_id != account_id:
        raise NumberEligibilityCaseNotFoundError(f"No eligibility case with id {case_id}")
    if not case.documents:
        raise NumberEligibilityDocumentRequiredError(
            "Upload a supporting document before submitting this case for review"
        )
    latest_doc = case.documents[-1]

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    email = end_user_attributes.get("email") or (owner.email if owner is not None else "")

    end_user = telecom.create_regulatory_end_user(
        friendly_name=f"{case.country} {end_user_type} - eligibility case {case.id}",
        end_user_type=end_user_type, attributes=end_user_attributes,
    )

    file_bytes = storage.download_object(latest_doc["storage_key"])
    supporting_document = telecom.upload_supporting_document(
        friendly_name=f"{case.country} supporting document - eligibility case {case.id}",
        document_type=latest_doc["document_type"],
        attributes={k: v for k, v in end_user_attributes.items() if k in ("first_name", "last_name")},
        file_bytes=file_bytes, content_type=latest_doc["content_type"],
    )

    bundle = telecom.create_regulatory_bundle(
        friendly_name=f"Zoiko Local {case.country} {case.number_type} - {case.id}",
        email=email,
        iso_country=case.country, end_user_type=end_user_type, number_type=case.number_type,
    )
    telecom.create_bundle_item_assignment(bundle["sid"], end_user["sid"])
    telecom.create_bundle_item_assignment(bundle["sid"], supporting_document["sid"])
    submitted = telecom.submit_bundle_for_review(bundle["sid"])

    case.twilio_end_user_sid = end_user["sid"]
    case.twilio_supporting_document_sid = supporting_document["sid"]
    case.twilio_bundle_sid = bundle["sid"]
    case.twilio_bundle_status = submitted["status"]
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_bundle_submitted",
        target=f"number_eligibility_case:{case.id}",
        after={"twilio_bundle_sid": bundle["sid"], "twilio_bundle_status": submitted["status"]},
    )
    return case


def sync_number_eligibility_bundle_status(db: Session, case_id: str, *, account_id: str, actor: str) -> NumberEligibilityCase:
    """On-demand check, not a webhook - Twilio's bundle review isn't
    instant, and depending on a webhook here would tie this to the same
    fragile local ngrok tunnel that already caused a real lost-webhook
    incident elsewhere in this project (see the video-recording sweep's
    own docstring) for an event that fires rarely enough this is simpler
    and just as reliable. Flipping to APPROVED here flows through the
    SAME NumberEligibilityCaseStatus.APPROVED status purchase_number
    already checks via has_approved_eligibility_case - no separate
    purchase-gate code needed for the Twilio-approval path."""
    case = _get_eligibility_case(db, case_id)
    if case.account_id != account_id:
        raise NumberEligibilityCaseNotFoundError(f"No eligibility case with id {case_id}")
    if not case.twilio_bundle_sid:
        raise NumberEligibilityDocumentRequiredError("This case hasn't been submitted to Twilio for review yet")

    result = telecom.get_bundle_status(case.twilio_bundle_sid)
    before_status = case.status
    case.twilio_bundle_status = result["status"]
    case.twilio_bundle_rejection_reason = result.get("rejection_reason")
    if result["status"] == "twilio-approved":
        case.status = NumberEligibilityCaseStatus.APPROVED
        case.resolved_at = datetime.now(timezone.utc)
    elif result["status"] == "twilio-rejected":
        case.status = NumberEligibilityCaseStatus.REJECTED
        case.review_notes = result.get("rejection_reason")
        case.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="number.eligibility_bundle_status_synced",
        target=f"number_eligibility_case:{case.id}",
        before={"status": before_status}, after={"status": case.status, "twilio_bundle_status": result["status"]},
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


def search_numbers(
    db: Session, country: str, *, account_id: str, number_type: str = "local", area_code: str | None = None,
) -> list[dict]:
    _assert_supported_country(db, country)
    _assert_market_activated(db, country, account_id)
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
    _assert_market_activated(db, country, account_id)
    now = datetime.now(timezone.utc)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    # Captured before any mutation below - a re-reservation of an expired/
    # released row (the final `else` branch) reassigns account_id, which
    # would otherwise leave a stale entry in the PREVIOUS account's cached
    # list (this number vanishing from account_id but never invalidated
    # there).
    previous_account_id = number.account_id if number is not None else None

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
    _invalidate_numbers_cache(account_id)
    if previous_account_id is not None and previous_account_id != account_id:
        _invalidate_numbers_cache(previous_account_id)
    log_event(
        db,
        actor_id=account_id,
        action="number.reserved",
        target_type="phone_number",
        target_id=number.id,
        metadata={"e164": e164},
    )
    publish_number_reserved(account_id, number_id=number.id, e164=e164, country=country)

    # Deferred import - see reactivate_numbers_for_account_by_staff's
    # comment on why (app.risk.service imports this module already).
    from app.risk.service import check_number_acquisition_velocity

    check_number_acquisition_velocity(db, account_id)

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
    # Production Readiness Standard doc §6.2 - re-checked here too (already
    # checked once at reserve_number time) as the same defense-in-depth
    # against the market being suspended in the gap between reservation
    # and purchase - "New sales/provisioning blocked immediately" on
    # SUSPENDED means an in-flight reservation shouldn't complete either.
    _assert_market_activated(db, number.country, account_id)
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
        _invalidate_numbers_cache(account_id)
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
            _invalidate_numbers_cache(account_id)
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

    # An approved eligibility case's Twilio bundle (if this number's
    # country/number_type has one - see submit_number_eligibility_bundle)
    # must be passed to Twilio's own purchase call, not just recorded on
    # our side - Twilio requires it for restricted number types regardless
    # of what our own eligibility case status says.
    eligibility_case = (
        db.query(NumberEligibilityCase)
        .filter(
            NumberEligibilityCase.phone_number_id == number.id,
            NumberEligibilityCase.status == NumberEligibilityCaseStatus.APPROVED,
        )
        .first()
    )
    bundle_sid = eligibility_case.twilio_bundle_sid if eligibility_case is not None else None

    try:
        bought = telecom.buy_number(e164, bundle_sid=bundle_sid)
    except telecom.TelecomError as e:
        # payment/provisioning failure must not strand the number silently —
        # release it back to Reserved so the customer can retry or it can expire
        number.status = PhoneNumberStatus.RESERVED
        number.provisioning_started_at = None
        # Architecture doc's "Provisioning Job... retry_count, error_code" -
        # see PhoneNumber.last_provisioning_error_code's docstring. Not
        # cleared on the eventual success - the count is a lifetime total,
        # not "attempts since the last failure."
        number.last_provisioning_error_code = str(e)[:100]
        number.provisioning_attempt_count += 1
        db.commit()
        _invalidate_numbers_cache(account_id)
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
    number.provisioning_attempt_count += 1
    number.next_renewal_at = now + timedelta(days=RENEWAL_PERIOD_DAYS)
    db.commit()
    db.refresh(number)
    _invalidate_numbers_cache(account_id)
    log_event(
        db, actor_id=account_id, action="number.activated",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "provider_sid": bought["sid"]},
    )
    publish_number_activated(account_id, number_id=number.id, e164=e164)

    # Commercial Billing Operating Standard doc §R6 - a real Twilio
    # purchase IS itself a legitimate verification/authorization source,
    # so this number's caller_identity is VERIFIED from the moment it's
    # genuinely provisioned, not left pending a separate step.
    _auto_verify_caller_identity(db, number, verification_source="platform_provisioned_purchase")

    # Trial-abuse step-up model (Production Readiness Standard doc) -
    # reaching ACTIVE is the "graduated into a real paying customer" moment.
    # Deferred import: app.risk.service imports this module (for
    # suspend_numbers_for_account_by_system), so the reverse import must
    # happen at call time, not at module load time.
    from app.risk.service import step_up_risk_state_after_purchase

    step_up_risk_state_after_purchase(db, account_id)

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        account = db.query(Account).filter(Account.id == account_id).first()
        notify_number_activated(
            db, account_id=account_id, account_email=owner.email, e164=e164,
            organization_name=account.name if account else "your organization",
        )

    return number


def _auto_verify_caller_identity(db: Session, number: PhoneNumber, *, verification_source: str) -> CallerIdentity:
    """Called at the moment a number is genuinely provisioned (real Twilio
    purchase - see purchase_number - or a completed port-in - see
    app.porting.service.complete_porting_request) - both are themselves
    legitimate verification/authorization sources (Commercial Billing
    Operating Standard doc §R6), so the caller_identity this platform
    creates is VERIFIED from the start rather than left pending a separate
    step. Upserts rather than always inserting, so re-running this (e.g. a
    retried purchase after a transient failure) can't create a duplicate
    row for the same number."""
    existing = db.query(CallerIdentity).filter(CallerIdentity.phone_number_id == number.id).first()
    now = datetime.now(timezone.utc)
    if existing is None:
        existing = CallerIdentity(phone_number_id=number.id, account_id=number.account_id)
        db.add(existing)
    existing.status = CallerIdentityStatus.VERIFIED
    existing.verification_source = verification_source
    existing.verified_at = now
    db.commit()
    db.refresh(existing)
    return existing


class CallerIdNotAuthorizedError(Exception):
    """Raised when routing would present a caller ID with no VERIFIED
    caller_identity record on file - Commercial Billing Operating Standard
    doc §R6 'routing rejects unauthorized combinations.'"""


def assert_caller_id_authorized(db: Session, phone_number_id: str) -> None:
    """Wired into place_outbound_call/place_outbound_call_for_account,
    alongside (not instead of) the existing ownership/ACTIVE-status check -
    ownership proves the account may use this number at all, this proves
    the specific caller-ID presentation is a formally verified one."""
    identity = db.query(CallerIdentity).filter(CallerIdentity.phone_number_id == phone_number_id).first()
    if identity is None or identity.status != CallerIdentityStatus.VERIFIED:
        status_label = identity.status.value if identity is not None else "unverified"
        raise CallerIdNotAuthorizedError(
            f"This number's caller ID is not authorized for outbound presentation ({status_label})"
        )
    if identity.expires_at is not None and identity.expires_at < datetime.now(timezone.utc):
        raise CallerIdNotAuthorizedError("This number's caller-ID verification has expired")


def revoke_caller_identity(
    db: Session, phone_number_id: str, *, staff_id: str, reason: str | None = None
) -> CallerIdentity:
    """Fraud/abuse response (e.g. a confirmed spoofing complaint) - blocks
    the number from outbound presentation without touching its ACTIVE
    billing/ownership status, which is a separate concern (see
    CallerIdentity's docstring) - a revoked number stays billed and owned,
    it just can't be used as an outbound caller ID until reinstated."""
    identity = db.query(CallerIdentity).filter(CallerIdentity.phone_number_id == phone_number_id).first()
    if identity is None:
        raise NumberConflictError(f"No caller-identity record exists for phone_number_id {phone_number_id}")
    before_status = identity.status
    identity.status = CallerIdentityStatus.REVOKED
    db.commit()
    db.refresh(identity)
    log_event(
        db, actor_id=staff_id, action="caller_identity.revoked",
        target_type="caller_identity", target_id=identity.id, reason=reason,
        metadata={"phone_number_id": phone_number_id, "before_status": before_status.value},
    )

    from app.risk.service import check_caller_id_change_velocity

    check_caller_id_change_velocity(db, identity.account_id)

    return identity


def reinstate_caller_identity(
    db: Session, phone_number_id: str, *, staff_id: str, reason: str | None = None
) -> CallerIdentity:
    """Reversal of revoke_caller_identity - a false positive or resolved
    dispute shouldn't need a brand new number purchase to restore calling."""
    identity = db.query(CallerIdentity).filter(CallerIdentity.phone_number_id == phone_number_id).first()
    if identity is None:
        raise NumberConflictError(f"No caller-identity record exists for phone_number_id {phone_number_id}")
    before_status = identity.status
    identity.status = CallerIdentityStatus.VERIFIED
    identity.verified_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(identity)
    log_event(
        db, actor_id=staff_id, action="caller_identity.reinstated",
        target_type="caller_identity", target_id=identity.id, reason=reason,
        metadata={"phone_number_id": phone_number_id, "before_status": before_status.value},
    )

    from app.risk.service import check_caller_id_change_velocity

    check_caller_id_change_velocity(db, identity.account_id)

    return identity


def create_number_purchase_checkout_session(db: Session, account_id: str, e164: str) -> dict:
    """Architecture doc §9: "Zoiko Local creates or updates plan, seat,
    number, and add-on entitlement events; ZoikoNex converts them into
    billing schedules" - a number's cost is meant to become a line item on
    the SAME ZoikoNex invoice as the plan fee, not a separate charge on a
    different rail. Previously this created a standalone one-time Stripe
    Checkout Session for any non-included number, completely disconnected
    from the subscription invoice - real gap fixed 2026-08-22.

    Commercial Billing Operating Standard doc's canonical transaction chain
    puts eligibility strictly BEFORE any charge ("eligibility -> customer
    authorization -> service entitlement -> ... -> charge/tax/fee result")
    - _assert_purchase_eligible runs here before the number is provisioned,
    same as it always has.

    Global Plans, Pricing & Commercial Launch Standard doc: the account's
    first number is included with a paid plan, not charged - unchanged by
    this fix. When billing_service.is_first_number_included says so, this
    calls purchase_number directly with no charge at all. Otherwise the
    number is STILL provisioned immediately (the customer isn't blocked
    waiting on a redirect/payment), but its cost is recorded via
    billing_service.record_pending_number_charge - run_billing_cycle's
    next run for this account adds it as a real invoice line item
    alongside the plan fee. See that function's docstring for why this
    doesn't yet implement the doc's recurring "$4.99/month" price for
    additional numbers."""
    # Commercial Billing Operating Standard doc §14/§T stopgap - see
    # Account.is_test's docstring. Overlaps with _assert_commercial_account
    # above (see TestAccountRestrictedError's docstring) - both are
    # checked, not consolidated, in this merge.
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is not None and account.is_test:
        raise TestAccountRestrictedError(f"Account {account_id} is flagged is_test and cannot create a live checkout session")
    _assert_commercial_account(db, account_id)

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

    rate = usage_service.get_number_rate(db, number.country, number.number_type)
    rate_cents = rate.recurring_price_cents if rate is not None else NUMBER_PURCHASE_PRICE_CENTS

    # Real bug fix: is_first_number_included's count-then-act check had no
    # lock on anything account-scoped, only on the individual PhoneNumber
    # row being purchased (see reserve_number's own SELECT...FOR UPDATE,
    # which protects a DIFFERENT race - two purchasers racing for the SAME
    # e164). Two concurrent checkouts for two DIFFERENT e164s on the same
    # account could both read included_count < seat_count as true before
    # either committed, and both take the zero-surcharge path below -
    # granting two free numbers instead of one. Locking the Account row
    # here serializes concurrent purchases for the SAME account (this
    # lock is released at purchase_number's own commit just below, in
    # every branch, once the number's status becomes one
    # get_included_number_ids counts) - it does not serialize purchases
    # across DIFFERENT accounts, which never contended in the first place.
    db.query(Account).filter(Account.id == account_id).with_for_update().first()

    if billing_service.is_first_number_included(db, account_id, exclude_number_id=number.id):
        surcharge_cents = max(0, rate_cents - NUMBER_INCLUSION_THRESHOLD_CENTS)
        if surcharge_cents == 0:
            log_event(
                db, actor_id=account_id, action="number.included_purchase",
                target_type="phone_number", target_id=number.id, metadata={"e164": e164},
            )
            included_number = purchase_number(db, account_id, e164)
            return {"id": None, "url": None, "included": True, "number": included_number}

        # Doc §5.1: included number, but its real rate is above the
        # inclusion threshold (a higher-cost or regulated number type) -
        # the customer still gets the included entitlement (provisioned
        # immediately below), but owes the incremental amount above the
        # threshold on their next invoice rather than getting the full
        # rate for free.
        purchased = purchase_number(db, account_id, e164)
        billing_service.record_pending_number_charge(
            db, account_id, charge_type="number_purchase", phone_number_id=purchased.id,
            description=f"Phone number purchase - {e164}", amount_minor_units=surcharge_cents,
            currency_code=NUMBER_PURCHASE_CURRENCY.upper(),
        )
        log_event(
            db, actor_id=account_id, action="number.included_purchase_surcharge_pending",
            target_type="phone_number", target_id=number.id,
            metadata={"e164": e164, "surcharge_cents": surcharge_cents},
        )
        return {
            "id": None, "url": None, "included": False, "number": purchased,
            "pending_charge_amount_minor_units": surcharge_cents,
        }

    purchased = purchase_number(db, account_id, e164)
    billing_service.record_pending_number_charge(
        db, account_id, charge_type="number_purchase", phone_number_id=purchased.id,
        description=f"Phone number purchase - {e164}", amount_minor_units=rate_cents,
        currency_code=NUMBER_PURCHASE_CURRENCY.upper(),
    )
    log_event(
        db, actor_id=account_id, action="number.purchase_pending_next_invoice",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "rate_cents": rate_cents},
    )
    return {
        "id": None, "url": None, "included": False, "number": purchased,
        "pending_charge_amount_minor_units": rate_cents,
    }


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
    except telecom.TelecomError as e:
        number.status = PhoneNumberStatus.RESERVED
        number.provisioning_started_at = None
        number.last_provisioning_error_code = str(e)[:100]
        number.provisioning_attempt_count += 1
        db.commit()
        _invalidate_numbers_cache(number.account_id)
        log_event(
            db, actor_id=staff_id, action="number.purchase_failed",
            target_type="phone_number", target_id=number.id, metadata={"e164": number.e164, "retried_by_staff": True},
        )
        raise

    number.status = PhoneNumberStatus.ACTIVE
    number.provider_sid = bought["sid"]
    number.reserved_until = None
    number.provisioning_started_at = None
    number.provisioning_attempt_count += 1
    number.next_renewal_at = datetime.now(timezone.utc) + timedelta(days=RENEWAL_PERIOD_DAYS)
    db.commit()
    db.refresh(number)
    _invalidate_numbers_cache(number.account_id)
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
    _invalidate_numbers_cache(number.account_id)
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
    must not invent a punitive failure mode that isn't backed by one.

    Global Plans, Pricing & Commercial Launch Standard doc §5.1 - "1
    standard eligible number per paid user... Additional standard local
    number: From $4.99/month." This DOES now record a real number_month
    UsageEvent for every renewal beyond the account's included-number pool,
    priced via NumberRate for visibility - see that table's docstring on
    why this still doesn't charge anyone (same gap as the rest of this
    function's existing docstring above). Which numbers count as
    "included" is resolved by billing_service.get_included_number_ids - the
    SAME function purchase-time eligibility uses (is_first_number_included)
    - not a separate inline query. An earlier version of this function
    computed its own answer (earliest ACTIVE number, no subscription-
    qualification check) that could disagree with purchase-time
    eligibility: a free-trial account's number renewed free forever
    despite the doc's own "paid user" requirement, and a suspended first
    number would silently hand free treatment to the second number
    instead."""
    number = db.query(PhoneNumber).filter(PhoneNumber.id == number_id).with_for_update().first()
    now = datetime.now(timezone.utc)
    if number is None or not is_number_billable(number.status) or (
        number.next_renewal_at is None or number.next_renewal_at > now
    ):
        raise NotDueForRenewalError(f"{number_id} is not currently due for renewal")

    due_at = number.next_renewal_at
    number.next_renewal_at = now + timedelta(days=RENEWAL_PERIOD_DAYS)
    db.commit()
    db.refresh(number)
    _invalidate_numbers_cache(number.account_id)
    log_event(
        db, actor_id=staff_id, action="number.renewed",
        target_type="phone_number", target_id=number.id,
        metadata={"e164": number.e164, "next_renewal_at": number.next_renewal_at.isoformat()},
    )

    included_number_ids = billing_service.get_included_number_ids(db, number.account_id)
    if number.id not in included_number_ids:
        usage_service.record_usage_event(
            db, account_id=number.account_id, event_type="number_month", quantity=1, unit="months",
            country_band=number.country, idempotency_key=f"number_month:{number.id}:{due_at.date().isoformat()}",
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
    _invalidate_numbers_cache(user.account_id)
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
    if numbers:
        _invalidate_numbers_cache(account_id)

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

        # TRUST-INT-003 "Calling Fraud Spend Spike" - the customer already
        # gets told (notify_number_suspended above); staff previously had
        # no signal at all that the risk engine had just auto-suspended a
        # whole account short of manually checking the fraud console.
        send_internal_alert(
            db, event_name="trust_int.fraud_spend_spike",
            summary=(
                f"Risk engine auto-suspended {len(numbers)} number(s) on account {account_id}: {reason}"
            ),
            console_link=f"{settings.public_base_url}/staff/fraud",
            tenant_reference=account_id,
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
    if numbers:
        _invalidate_numbers_cache(account_id)

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

    # Trial-abuse step-up model - lifts a SUSPENDED_FRAUD account's risk
    # tier back to whatever its KYC/purchase history actually supports, now
    # that staff has decided its numbers are safe to reactivate. Deferred
    # import - see step_up_risk_state_after_purchase's call site above.
    from app.risk.service import restore_risk_state_after_reinstatement

    restore_risk_state_after_reinstatement(db, account_id, actor=staff_id)

    return numbers


def release_numbers_for_account_by_system(
    db: Session, account_id: str, *, actor: str, reason: str | None = None
) -> list[PhoneNumber]:
    """System/staff-initiated bulk deprovisioning, no per-number User in the
    loop - used by billing.service.terminate_subscription (Commercial
    Billing Operating Standard doc §M3 "termination... provider
    deprovisioning") since a terminated account has no future Owner session
    to call cancel_number itself. Releases every ACTIVE/SUSPENDED number on
    the account; already-cancelled numbers are left alone.

    A single number's Twilio release failing doesn't abort the rest - the
    account is being torn down regardless, and leaving 9 of 10 numbers
    stuck ACTIVE because the 10th's provider call failed would be worse
    than one number needing a manual follow-up release."""
    numbers = (
        db.query(PhoneNumber)
        .filter(
            PhoneNumber.account_id == account_id,
            PhoneNumber.status.in_([PhoneNumberStatus.ACTIVE, PhoneNumberStatus.SUSPENDED]),
        )
        .with_for_update()
        .all()
    )
    released: list[PhoneNumber] = []
    for number in numbers:
        try:
            if number.provider_sid:
                telecom.release_number(number.provider_sid)
        except telecom.TelecomError as e:
            log_event(
                db, actor_id=account_id, action="number.release_failed",
                target_type="phone_number", target_id=number.id, reason=str(e), metadata={"e164": number.e164},
            )
            continue
        number.status = PhoneNumberStatus.CANCELLED
        number.cancelled_at = datetime.now(timezone.utc)
        released.append(number)

    db.commit()
    if released:
        _invalidate_numbers_cache(account_id)

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    for number in released:
        db.refresh(number)
        log_event(
            db, actor_id=actor, action="number.released_for_termination",
            target_type="phone_number", target_id=number.id, reason=reason, metadata={"e164": number.e164},
        )
        # No publish_number_* event here - matches cancel_number above,
        # which doesn't publish one either for an individual release;
        # publish_number_suspended's semantics don't fit a CANCELLED number.
        if owner is not None:
            notify_number_released(db, account_id=account_id, account_email=owner.email, e164=number.e164)

    return released


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
    _invalidate_numbers_cache(user.account_id)
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
    escalation_phone_number: str | None = None,
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

    if ai_receptionist_enabled and not number.ai_receptionist_enabled and not (
        billing_service.is_ai_receptionist_enabled_for_account(db, user.account_id)
    ):
        raise billing_service.AiReceptionistNotEntitledError(
            "Your plan doesn't include AI Receptionist - upgrade or add the AI Receptionist add-on to enable it."
        )

    try:
        ZoneInfo(business_hours_timezone)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Unknown timezone: {business_hours_timezone}") from e

    if escalation_user_id is not None:
        nominee = db.query(User).filter(User.id == escalation_user_id, User.account_id == user.account_id).first()
        if nominee is None:
            raise NumberConflictError(f"No team member with id {escalation_user_id} on this account")

    # ZL-COM-ENT-001 §7 matrix: "Business-hours & team routing: No (Starter)
    # / Yes (Business+)" - configuring business hours at all (not just
    # enabling the feature flag) is the Business+ capability. Checked only
    # when hours are actually being SET (not on every save with hours left
    # None) so a Starter account clearing/leaving hours unset is unaffected.
    if (business_hours_start is not None or business_hours_end is not None) and not billing_service.has_entitlement(
        db, user.account_id, "routing.business_hours"
    ):
        raise billing_service.EntitlementRequiredError(
            "routing.business_hours", billing_service.get_or_create_subscription(db, user.account_id).plan_code
        )

    number.forwarding_number = forwarding_number
    number.business_hours_start = business_hours_start
    number.business_hours_end = business_hours_end
    number.business_hours_timezone = business_hours_timezone
    number.ai_receptionist_enabled = ai_receptionist_enabled
    number.escalation_user_id = escalation_user_id
    number.escalation_phone_number = escalation_phone_number
    number.whatsapp_enabled = whatsapp_enabled
    number.sms_enabled = sms_enabled
    db.commit()
    db.refresh(number)
    _invalidate_numbers_cache(user.account_id)
    log_event(
        db, actor_id=user.id, action="number.routing_configured",
        target_type="phone_number", target_id=number.id,
        metadata={
            "e164": e164,
            "forwarding_number": forwarding_number,
            "escalation_user_id": escalation_user_id,
            "escalation_phone_number": escalation_phone_number,
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

    # ZL-COM-ENT-001 §7 matrix: "Shared call handling: No (Starter) / Yes
    # (Business+)" - a single destination is just personal forwarding
    # (already available to every plan via forwarding_number); 2+
    # destinations ringing simultaneously is the actual "shared handling"
    # capability being gated here.
    if len(destinations) > 1 and not billing_service.has_entitlement(db, user.account_id, "routing.shared_handling"):
        raise billing_service.EntitlementRequiredError(
            "routing.shared_handling", billing_service.get_or_create_subscription(db, user.account_id).plan_code
        )

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
    _invalidate_numbers_cache(user.account_id)

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
    _invalidate_numbers_cache(user.account_id)

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


def _numbers_cache_key(account_id: str) -> str:
    return f"numbers:list:{account_id}"


# Short TTL as a safety net, but this list is actively invalidated at
# every write site that changes a PhoneNumber row for the account (see
# _invalidate_numbers_cache's call sites below) - the dashboard already
# refetches this list immediately after every action a customer takes
# (purchase/suspend/cancel/assign/routing/...), so a cache that only
# expired on a timer without invalidation would show a customer stale
# state right after their own action, which is worse than no cache at all.
_NUMBERS_CACHE_TTL_SECONDS = 30


def _serialize_phone_number(n: PhoneNumber) -> dict:
    return {
        "id": n.id,
        "e164": n.e164,
        "country": n.country,
        "provider": n.provider,
        "provider_sid": n.provider_sid,
        "status": n.status.value,
        "account_id": n.account_id,
        "reserved_until": n.reserved_until.isoformat() if n.reserved_until else None,
        "number_type": n.number_type,
        "cancelled_at": n.cancelled_at.isoformat() if n.cancelled_at else None,
        "provisioning_started_at": n.provisioning_started_at.isoformat() if n.provisioning_started_at else None,
        "assigned_user_id": n.assigned_user_id,
        "forwarding_number": n.forwarding_number,
        "business_hours_start": n.business_hours_start.isoformat() if n.business_hours_start else None,
        "business_hours_end": n.business_hours_end.isoformat() if n.business_hours_end else None,
        "business_hours_timezone": n.business_hours_timezone,
        "ai_receptionist_enabled": n.ai_receptionist_enabled,
        "escalation_user_id": n.escalation_user_id,
        "ivr_greeting": n.ivr_greeting,
        "next_renewal_at": n.next_renewal_at.isoformat() if n.next_renewal_at else None,
        "call_flow_id": n.call_flow_id,
        "whatsapp_enabled": n.whatsapp_enabled,
        "sms_enabled": n.sms_enabled,
        "created_at": n.created_at.isoformat() if n.created_at else None,
    }


def _deserialize_phone_number(data: dict) -> PhoneNumber:
    from datetime import time as time_cls

    return PhoneNumber(
        id=data["id"],
        e164=data["e164"],
        country=data["country"],
        provider=data["provider"],
        provider_sid=data["provider_sid"],
        status=PhoneNumberStatus(data["status"]),
        account_id=data["account_id"],
        reserved_until=datetime.fromisoformat(data["reserved_until"]) if data["reserved_until"] else None,
        number_type=data["number_type"],
        cancelled_at=datetime.fromisoformat(data["cancelled_at"]) if data["cancelled_at"] else None,
        provisioning_started_at=(
            datetime.fromisoformat(data["provisioning_started_at"]) if data["provisioning_started_at"] else None
        ),
        assigned_user_id=data["assigned_user_id"],
        forwarding_number=data["forwarding_number"],
        business_hours_start=time_cls.fromisoformat(data["business_hours_start"]) if data["business_hours_start"] else None,
        business_hours_end=time_cls.fromisoformat(data["business_hours_end"]) if data["business_hours_end"] else None,
        business_hours_timezone=data["business_hours_timezone"],
        ai_receptionist_enabled=data["ai_receptionist_enabled"],
        escalation_user_id=data["escalation_user_id"],
        ivr_greeting=data["ivr_greeting"],
        next_renewal_at=datetime.fromisoformat(data["next_renewal_at"]) if data["next_renewal_at"] else None,
        call_flow_id=data["call_flow_id"],
        whatsapp_enabled=data["whatsapp_enabled"],
        sms_enabled=data["sms_enabled"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_numbers_cache(account_id: str) -> None:
    cache_delete(_numbers_cache_key(account_id))


def _list_account_numbers_uncached(db: Session, account_id: str) -> list[PhoneNumber]:
    return db.query(PhoneNumber).filter(PhoneNumber.account_id == account_id).all()


def list_account_numbers(db: Session, account_id: str, *, user: User) -> list[PhoneNumber]:
    """Owner/Admin see every number on the account. A plain Member only sees
    numbers assigned to them - unassigned numbers aren't theirs to manage
    yet, they haven't been handed anything. Cached account-wide (the
    unfiltered, Owner/Admin view) so every member of the same account
    shares one cache entry; the Member filter is applied in Python after
    the cache lookup rather than being baked into the cache key."""
    cache_key = _numbers_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        numbers = [_deserialize_phone_number(row) for row in cached]
    else:
        numbers = _list_account_numbers_uncached(db, account_id)
        cache_set(cache_key, [_serialize_phone_number(n) for n in numbers], ttl_seconds=_NUMBERS_CACHE_TTL_SECONDS)
    if user.role == UserRole.MEMBER:
        numbers = [n for n in numbers if n.assigned_user_id == user.id]
    return numbers


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
    _invalidate_numbers_cache(account_id)

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
