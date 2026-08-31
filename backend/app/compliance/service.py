import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.compliance.models import ComplianceCase, ComplianceCaseStatus, ComplianceRule
from app.events.service import (
    publish_compliance_case_approved,
    publish_compliance_case_expired,
    publish_compliance_case_rejected,
    publish_compliance_case_required,
)
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.kyc import stripe_identity
from app.integrations.storage import s3 as storage
from app.notifications.service import (
    notify_compliance_case_approved,
    notify_compliance_case_rejected,
    notify_compliance_information_required,
    notify_organization_verification_submitted,
)

# Kept small and conservative - these are ID/business documents reviewed by
# a human compliance officer, not a general file-upload feature.
_ALLOWED_DOCUMENT_CONTENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}
_MAX_DOCUMENT_SIZE_BYTES = 10 * 1024 * 1024

# No doc gives an exact number here - same "reasonable Phase-1 default,
# clearly labeled as such, not invented precision" posture as
# billing_service.GRACE_PERIOD_DAYS/numbering.numbers.service.QUARANTINE_DAYS.
# A case sitting PENDING this long without ever being approved/rejected is
# treated as stale rather than left open forever - see
# sweep_expired_compliance_cases.
COMPLIANCE_CASE_EXPIRY_DAYS = 90

# Stripe Identity VerificationSession status -> our case status. "requires_input"
# is overloaded - confirmed live against a real submission: it's BOTH the
# session's initial status right after creation (nothing submitted yet) AND
# where a session lands after a genuine failed verification (Stripe's
# testmode auto-marks document submissions "unverified" unless you force a
# specific outcome, and that failure surfaces as requires_input + last_error,
# not as its own terminal status). handle_stripe_identity_webhook treats
# requires_input as a rejection ONLY when a last_error reason is attached -
# a bare requires_input with no error is genuinely "not submitted yet".
_APPROVING_STATUSES = {"verified"}
_REJECTING_STATUSES = {"canceled"}


def _account_owner_email(db: Session, account_id: str) -> str | None:
    from app.numbering.identity.models import User, UserRole

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    return owner.email if owner else None


def get_active_rules(db: Session, country: str) -> list[ComplianceRule]:
    return (
        db.query(ComplianceRule)
        .filter(ComplianceRule.country == country.upper(), ComplianceRule.is_active.is_(True))
        .all()
    )


def list_all_rules(db: Session) -> list[ComplianceRule]:
    """Staff-facing view across every country/requirement type, active or
    not - unlike get_active_rules (single-country, active-only, used by the
    live purchase-gating check), this is the management surface a staff
    member needs to see what's configured before adding or deactivating a
    rule."""
    return db.query(ComplianceRule).order_by(ComplianceRule.country, ComplianceRule.requirement_type).all()


def upsert_compliance_rule(
    db: Session, *, country: str, requirement_type: str, required_documents: list[str], is_active: bool, actor: str
) -> ComplianceRule:
    """Staff-tunable, same "rules as data" doctrine as risk.upsert_fraud_rule
    - deliberately NOT bulk-seeded by a migration (see a7be96c38a85's
    docstring for why that broke the test suite): a signal/rule with no row
    here simply never gates anything (fail-open by omission), and a real
    deployment populates real rules deliberately through this endpoint."""
    rule = (
        db.query(ComplianceRule)
        .filter(ComplianceRule.country == country.upper(), ComplianceRule.requirement_type == requirement_type)
        .first()
    )
    if rule is None:
        rule = ComplianceRule(
            country=country.upper(), requirement_type=requirement_type,
            required_documents=required_documents, is_active=is_active,
        )
        db.add(rule)
    else:
        rule.required_documents = required_documents
        rule.is_active = is_active
    db.commit()
    db.refresh(rule)
    log_event(
        db, actor=actor, action="compliance.rule_updated", target=f"compliance_rule:{rule.id}",
        after={"country": rule.country, "requirement_type": requirement_type, "is_active": is_active},
    )
    return rule


def is_requirement_active(db: Session, country: str, requirement_type: str) -> bool:
    return (
        db.query(ComplianceRule)
        .filter(
            ComplianceRule.country == country.upper(),
            ComplianceRule.requirement_type == requirement_type,
            ComplianceRule.is_active.is_(True),
        )
        .first()
        is not None
    )


def has_approved_case(db: Session, *, account_id: str, jurisdiction: str, requirement_type: str) -> bool:
    return (
        db.query(ComplianceCase)
        .filter(
            ComplianceCase.account_id == account_id,
            ComplianceCase.jurisdiction == jurisdiction.upper(),
            ComplianceCase.requirement_type == requirement_type,
            ComplianceCase.status == ComplianceCaseStatus.APPROVED,
        )
        .first()
        is not None
    )


class NumberNotOwnedError(Exception):
    """Raised when a case is opened against a number_id that either doesn't
    exist or isn't owned by the account opening the case."""


def open_compliance_case(
    db: Session,
    *,
    account_id: str,
    jurisdiction: str,
    requirement_type: str,
    actor: str,
    number_id: str | None = None,
) -> ComplianceCase:
    if number_id is not None:
        from app.numbering.numbers.models import PhoneNumber

        owned = (
            db.query(PhoneNumber)
            .filter(PhoneNumber.id == number_id, PhoneNumber.account_id == account_id)
            .first()
        )
        if owned is None:
            raise NumberNotOwnedError(number_id)

    case = ComplianceCase(
        account_id=account_id,
        number_id=number_id,
        jurisdiction=jurisdiction.upper(),
        requirement_type=requirement_type,
        expires_at=datetime.now(timezone.utc) + timedelta(days=COMPLIANCE_CASE_EXPIRY_DAYS),
    )
    db.add(case)
    db.commit()
    db.refresh(case)
    _invalidate_cases_cache(account_id)

    log_event(
        db,
        actor=actor,
        action="compliance.case_opened",
        target=f"compliance_case:{case.id}",
        after={"case_id": case.id, "jurisdiction": case.jurisdiction, "requirement_type": requirement_type},
    )
    publish_compliance_case_required(
        account_id, case_id=case.id, jurisdiction=case.jurisdiction, requirement_type=requirement_type,
    )

    owner_email = _account_owner_email(db, account_id)
    if owner_email:
        notify_compliance_information_required(
            db, account_id=account_id, account_email=owner_email,
            jurisdiction=case.jurisdiction, requirement_type=requirement_type, case_reference=case.id,
        )

    return case


def _cases_cache_key(account_id: str) -> str:
    return f"compliance_cases:list:{account_id}"


# Small per-account row counts (typically a handful of cases at most) mean
# the perf win here is modest, but the write-site count is small too (only
# 5 real mutators - open/submit_document/approve/reject/start_kyc_
# verification), so it costs little to close the same gap the higher-
# traffic lists already closed.
_CASES_CACHE_TTL_SECONDS = 30


def _serialize_case(c: ComplianceCase) -> dict:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "number_id": c.number_id,
        "jurisdiction": c.jurisdiction,
        "requirement_type": c.requirement_type,
        "status": c.status.value,
        "documents": c.documents,
        "expires_at": c.expires_at.isoformat() if c.expires_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "kyc_inquiry_id": c.kyc_inquiry_id,
    }


def _deserialize_case(data: dict) -> ComplianceCase:
    return ComplianceCase(
        id=data["id"],
        account_id=data["account_id"],
        number_id=data["number_id"],
        jurisdiction=data["jurisdiction"],
        requirement_type=data["requirement_type"],
        status=ComplianceCaseStatus(data["status"]),
        documents=data["documents"],
        expires_at=datetime.fromisoformat(data["expires_at"]) if data["expires_at"] else None,
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
        kyc_inquiry_id=data["kyc_inquiry_id"],
    )


def _invalidate_cases_cache(account_id: str) -> None:
    cache_delete(_cases_cache_key(account_id))


def list_cases_for_account(db: Session, account_id: str) -> list[ComplianceCase]:
    cache_key = _cases_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_case(row) for row in cached]
    cases = (
        db.query(ComplianceCase)
        .filter(ComplianceCase.account_id == account_id)
        .order_by(ComplianceCase.created_at.desc())
        .all()
    )
    cache_set(cache_key, [_serialize_case(c) for c in cases], ttl_seconds=_CASES_CACHE_TTL_SECONDS)
    return cases


def list_all_cases(db: Session, status: str | None = None) -> list[dict]:
    """Staff-only view across every account - joins in the account name
    and owner email so a reviewer has enough context without a second
    lookup. Not exposed to customers (see routes.py: get_current_staff)."""
    from app.numbering.identity.models import Account, User, UserRole

    query = (
        db.query(ComplianceCase, Account.name, User.email)
        .join(Account, Account.id == ComplianceCase.account_id)
        .join(User, (User.account_id == Account.id) & (User.role == UserRole.OWNER))
    )
    if status:
        query = query.filter(ComplianceCase.status == ComplianceCaseStatus(status))

    rows = query.order_by(ComplianceCase.created_at.desc()).all()
    return [
        {
            "id": case.id,
            "account_id": case.account_id,
            "account_name": account_name,
            "account_owner_email": owner_email,
            "number_id": case.number_id,
            "jurisdiction": case.jurisdiction,
            "requirement_type": case.requirement_type,
            "status": case.status,
            "documents": case.documents,
            "expires_at": case.expires_at,
            "created_at": case.created_at,
            "kyc_inquiry_id": case.kyc_inquiry_id,
        }
        for case, account_name, owner_email in rows
    ]


def get_case(db: Session, case_id: str) -> ComplianceCase | None:
    try:
        uuid.UUID(case_id)
    except ValueError:
        return None  # not a valid UUID at all - can't possibly match a row
    return db.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()


class UnsupportedDocumentTypeError(Exception):
    """Raised when an uploaded compliance document isn't a PDF or image."""


class DocumentTooLargeError(Exception):
    """Raised when an uploaded compliance document exceeds the size cap."""


class DocumentNotFoundError(Exception):
    """Raised when a document index doesn't exist on this case, or refers
    to a legacy metadata-only entry with no file actually in storage."""


def submit_document(
    db: Session,
    case: ComplianceCase,
    *,
    document_type: str,
    filename: str,
    content_type: str,
    data: bytes,
    actor: str,
) -> ComplianceCase:
    """Stores the actual uploaded file in the configured object storage
    (same Provider Gateway used for recordings) and records its metadata on
    the case - the storage key is server-generated from the case id, never
    taken from client input, so a case's documents can't be pointed at an
    arbitrary bucket path."""
    if content_type not in _ALLOWED_DOCUMENT_CONTENT_TYPES:
        raise UnsupportedDocumentTypeError(
            f"{content_type} is not an accepted document type - upload a PDF, JPEG, or PNG"
        )
    if len(data) > _MAX_DOCUMENT_SIZE_BYTES:
        raise DocumentTooLargeError("Document exceeds the 10MB upload limit")

    storage_key = f"compliance-documents/{case.id}/{uuid.uuid4()}-{filename}"
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
    _invalidate_cases_cache(case.account_id)

    log_event(
        db,
        actor=actor,
        action="compliance.document_submitted",
        target=f"compliance_case:{case.id}",
        after={"document_type": document_type, "filename": filename},
    )

    owner_email = _account_owner_email(db, case.account_id)
    if owner_email:
        from app.numbering.identity.models import Account

        account = db.query(Account).filter(Account.id == case.account_id).first()
        notify_organization_verification_submitted(
            db, account_id=case.account_id, account_email=owner_email,
            organization_name=account.name if account else "your organization",
            case_reference=case.id,
        )

    return case


def get_document_download_url(case: ComplianceCase, document_index: int) -> str:
    if document_index < 0 or document_index >= len(case.documents):
        raise DocumentNotFoundError(f"Document {document_index} does not exist on case {case.id}")
    storage_key = case.documents[document_index].get("storage_key")
    if not storage_key:
        raise DocumentNotFoundError(f"Document {document_index} on case {case.id} has no stored file")
    return storage.generate_presigned_url(storage_key)


class ComplianceCaseAlreadyDecidedError(Exception):
    """Raised by approve_case/reject_case when the case isn't PENDING -
    APPROVED/REJECTED/EXPIRED are all terminal decisions (same "only
    PENDING cases are in scope" rule expire_overdue_cases already applies).
    Real gap fix: neither function used to check this at all, unlike
    every sibling resolver in this codebase (resolve_erasure_request
    checks status != PENDING, resolve_fraud_case checks status != OPEN) -
    Stripe redelivering the same identity.verification_session.verified
    webhook (a documented Stripe behavior) re-ran approve_case a second
    time, re-publishing Kafka events, re-sending the approval email, and
    re-running the risk step-up; a stray double-click or an out-of-order
    webhook could also silently reverse an already-REJECTED case."""


def approve_case(db: Session, case: ComplianceCase, *, actor: str) -> ComplianceCase:
    if case.status != ComplianceCaseStatus.PENDING:
        raise ComplianceCaseAlreadyDecidedError(
            f"Case {case.id} is already {case.status.value} - cannot approve a case that isn't pending"
        )
    before_status = case.status
    case.status = ComplianceCaseStatus.APPROVED
    db.commit()
    db.refresh(case)
    _invalidate_cases_cache(case.account_id)

    log_event(
        db,
        actor=actor,
        action="compliance.case_approved",
        target=f"compliance_case:{case.id}",
        before={"status": before_status},
        after={"status": case.status},
    )

    publish_compliance_case_approved(
        case.account_id, case_id=case.id, jurisdiction=case.jurisdiction, requirement_type=case.requirement_type,
    )
    # Deferred import: app.risk.service imports this module (has_approved_
    # case/is_requirement_active via app.numbering.numbers.service), so the
    # reverse import must happen at call time, not at module load time.
    from app.risk.service import step_up_risk_state_after_kyc_approval

    step_up_risk_state_after_kyc_approval(db, case.account_id)

    owner_email = _account_owner_email(db, case.account_id)
    if owner_email:
        notify_compliance_case_approved(
            db,
            account_id=case.account_id,
            account_email=owner_email,
            jurisdiction=case.jurisdiction,
            requirement_type=case.requirement_type,
        )

    return case


def reject_case(db: Session, case: ComplianceCase, *, actor: str, reason: str | None = None) -> ComplianceCase:
    if case.status != ComplianceCaseStatus.PENDING:
        raise ComplianceCaseAlreadyDecidedError(
            f"Case {case.id} is already {case.status.value} - cannot reject a case that isn't pending"
        )
    before_status = case.status
    case.status = ComplianceCaseStatus.REJECTED
    db.commit()
    db.refresh(case)
    _invalidate_cases_cache(case.account_id)

    log_event(
        db,
        actor=actor,
        action="compliance.case_rejected",
        target=f"compliance_case:{case.id}",
        reason=reason,
        before={"status": before_status},
        after={"status": case.status},
    )

    publish_compliance_case_rejected(
        case.account_id, case_id=case.id, jurisdiction=case.jurisdiction,
        requirement_type=case.requirement_type, reason=reason,
    )

    owner_email = _account_owner_email(db, case.account_id)
    if owner_email:
        notify_compliance_case_rejected(
            db,
            account_id=case.account_id,
            account_email=owner_email,
            jurisdiction=case.jurisdiction,
            requirement_type=case.requirement_type,
            reason=reason,
        )

    return case


def expire_overdue_cases(db: Session) -> dict[str, int]:
    """Finds every PENDING case past its expires_at and transitions it to
    EXPIRED. Called from app.ops.scheduled_reconciliation's daily run (same
    posture as app.retention.service.purge_expired_recordings) - still
    depends on that script actually being scheduled externally (see its own
    docstring), this function has no timer of its own.

    Only PENDING cases are in scope - an APPROVED/REJECTED case has already
    reached a terminal human decision, and expires_at describes how long the
    verification window stays open, not how long the decision stays valid.
    """
    now = datetime.now(timezone.utc)
    overdue = (
        db.query(ComplianceCase)
        .filter(
            ComplianceCase.status == ComplianceCaseStatus.PENDING,
            ComplianceCase.expires_at.isnot(None),
            ComplianceCase.expires_at < now,
        )
        .all()
    )
    for case in overdue:
        case.status = ComplianceCaseStatus.EXPIRED
        db.commit()
        _invalidate_cases_cache(case.account_id)

        log_event(
            db,
            actor_id=case.account_id,
            action="compliance.case_expired",
            target_type="compliance_case",
            target_id=case.id,
            before={"status": ComplianceCaseStatus.PENDING.value},
            after={"status": case.status.value},
        )
        publish_compliance_case_expired(
            case.account_id, case_id=case.id, jurisdiction=case.jurisdiction, requirement_type=case.requirement_type,
        )

    return {"expired": len(overdue)}


class KYCAlreadyApprovedError(Exception):
    """Raised when someone tries to restart verification on a case that's
    already passed - re-verifying an approved case is pointless and risks
    a flaky retry overwriting a correct decision with a worse one."""


def start_kyc_verification(db: Session, case: ComplianceCase, *, actor: str) -> dict:
    """Kicks off a real Stripe Identity VerificationSession for this case
    and returns the hosted-flow link the customer opens to complete it.
    The Stripe webhook (handle_stripe_identity_webhook) is what actually
    approves/rejects the case - this call only starts the process.

    Allowed from PENDING (first attempt or resuming an unfinished one) and
    REJECTED (retry after a real failure - the gap a customer would
    otherwise be stuck on with no self-service way forward). Blocked from
    APPROVED."""
    if case.status == ComplianceCaseStatus.APPROVED:
        raise KYCAlreadyApprovedError(f"Case {case.id} is already approved - verification cannot be restarted")

    session = stripe_identity.create_verification_session(reference_id=case.id)
    inquiry_id = session["id"]
    verification_url = session["url"]

    case.kyc_inquiry_id = inquiry_id
    if case.status == ComplianceCaseStatus.REJECTED:
        # A retry in progress isn't accurately "rejected" anymore - leaving
        # the old status would show a stale verdict in the UI while the
        # new attempt is still pending a fresh decision.
        case.status = ComplianceCaseStatus.PENDING
    db.commit()
    _invalidate_cases_cache(case.account_id)

    log_event(
        db,
        actor=actor,
        action="compliance.kyc_started",
        target=f"compliance_case:{case.id}",
        after={"inquiry_id": inquiry_id},
    )
    return {"inquiry_id": inquiry_id, "verification_url": verification_url}


def handle_stripe_identity_webhook(
    db: Session, session_id: str, status: str, last_error_reason: str | None = None
) -> ComplianceCase | None:
    """Maps a real Stripe Identity verification decision back onto our
    compliance case. "processing", and "requires_input" with no error yet,
    are no-ops - left for the customer to retry or a human reviewer via the
    existing manual approve/reject endpoints."""
    case = db.query(ComplianceCase).filter(ComplianceCase.kyc_inquiry_id == session_id).first()
    if case is None:
        return None

    # Idempotent against Stripe's own documented webhook-redelivery
    # behavior - a second delivery of the same completed/failed event must
    # not re-approve/re-reject (re-publishing Kafka events, re-sending the
    # notification email, re-running the risk step-up) a case this webhook
    # already decided. Not an error worth surfacing to Stripe as a
    # failure (which would just trigger more retries) - the case is
    # already in the state this event asked for.
    if case.status != ComplianceCaseStatus.PENDING:
        return case

    if status in _APPROVING_STATUSES:
        return approve_case(db, case, actor="stripe_identity_webhook")
    if status in _REJECTING_STATUSES:
        return reject_case(db, case, actor="stripe_identity_webhook", reason=f"Stripe Identity verification {status}")
    if status == "requires_input" and last_error_reason:
        return reject_case(db, case, actor="stripe_identity_webhook", reason=last_error_reason)
    return case
