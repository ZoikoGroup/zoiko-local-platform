from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.security import hash_password, verify_password
from app.events.service import publish_account_billing_classification_updated
from app.numbering.identity.models import Account, User, UserRole
from app.numbering.numbers.models import PhoneNumber
from app.numbering.numbers.service import list_due_renewals as _list_due_renewals
from app.numbering.numbers.service import list_stuck_provisioning as _list_stuck_provisioning
from app.staff.models import PlatformStaff, PlatformStaffRole, StaffCapabilityGrant

# The capability that governs the grant/revoke endpoints below. Protected
# against ever reaching zero grants (see revoke_capability) - if no role
# could manage the matrix anymore, fixing that would need direct database
# access instead of the UI meant to make this a data change, not a
# redeploy.
MATRIX_MANAGEMENT_CAPABILITY = "staff.manage_capabilities"


class LastGrantRemovalError(Exception):
    """Raised when revoking a grant would leave MATRIX_MANAGEMENT_CAPABILITY
    with zero roles able to manage the matrix at all - an unrecoverable
    lockout short of direct database access."""


def create_staff(db: Session, email: str, password: str, role: PlatformStaffRole) -> PlatformStaff:
    """role has no default - there's no public staff signup endpoint (staff
    are provisioned internally, see app/seed.py), so every call site must
    consciously pick a role rather than silently inheriting a default."""
    existing = db.query(PlatformStaff).filter(PlatformStaff.email == email).first()
    if existing:
        raise ValueError("A staff account with this email already exists")

    staff = PlatformStaff(email=email, hashed_password=hash_password(password), role=role)
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def authenticate_staff(db: Session, email: str, password: str) -> PlatformStaff | None:
    staff = db.query(PlatformStaff).filter(PlatformStaff.email == email).first()
    if not staff or not staff.is_active or not verify_password(password, staff.hashed_password):
        return None
    return staff


def list_accounts_overview(db: Session) -> list[dict]:
    """Cross-account visibility for Zoiko ops - who owns this account, how
    many team members and numbers it has. Staff-only (see routes.py:
    get_current_staff), same rationale as compliance's list_all_cases: a
    customer must never see another customer's account, but ops legitimately
    needs to look one up without a database console.
    """
    member_counts = dict(
        db.query(User.account_id, func.count(User.id)).group_by(User.account_id).all()
    )
    number_counts = dict(
        db.query(PhoneNumber.account_id, func.count(PhoneNumber.id)).group_by(PhoneNumber.account_id).all()
    )
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.role == UserRole.OWNER).all()
    }

    accounts = db.query(Account).order_by(Account.created_at.desc()).all()
    return [
        {
            "id": account.id,
            "name": account.name,
            "account_type": account.account_type,
            "owner_email": owners.get(account.id),
            "member_count": member_counts.get(account.id, 0),
            "number_count": number_counts.get(account.id, 0),
            "billing_classification": account.billing_classification,
            "billing_source": account.billing_source,
            "is_test": account.is_test,
            "legal_hold": account.legal_hold,
            "legal_hold_reference": account.legal_hold_reference,
            "created_at": account.created_at,
        }
        for account in accounts
    ]


class AccountNotFoundError(Exception):
    """Raised when an account id doesn't exist."""


def get_account_overview(db: Session, account_id: str) -> dict:
    """Single-account counterpart to list_accounts_overview - avoids
    pulling every account just to return one, e.g. right after a billing-
    classification update."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    return {
        "id": account.id,
        "name": account.name,
        "account_type": account.account_type,
        "owner_email": owner.email if owner else None,
        "member_count": db.query(User).filter(User.account_id == account_id).count(),
        "number_count": db.query(PhoneNumber).filter(PhoneNumber.account_id == account_id).count(),
        "billing_classification": account.billing_classification,
        "billing_source": account.billing_source,
        "is_test": account.is_test,
        "legal_hold": account.legal_hold,
        "legal_hold_reference": account.legal_hold_reference,
        "created_at": account.created_at,
    }


def update_account_billing_classification(
    db: Session, account_id: str, *, billing_classification, billing_source, actor: str
) -> Account:
    """Commercial Billing Operating Standard doc's P0 blocker: every
    account needs a real billing_classification/billing_source, settable
    by staff for the non-default cases (marking an account DEMO, SANDBOX,
    a PARTNER_SPONSORED deal, etc.) - see Account model's docstring for
    why the public signup path only ever creates COMMERCIAL_STANDALONE."""
    account = db.query(Account).filter( Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")

    before = {"billing_classification": account.billing_classification.value, "billing_source": account.billing_source.value}
    account.billing_classification = billing_classification
    account.billing_source = billing_source
    db.commit()
    db.refresh(account)
    log_event(
        db, actor=actor, action="account.billing_classification_updated", target=f"account:{account.id}",
        before=before,
        after={"billing_classification": billing_classification.value, "billing_source": billing_source.value},
    )
    publish_account_billing_classification_updated(
        account_id, billing_classification=billing_classification.value, billing_source=billing_source.value,
    )
    return account


def set_account_test_flag(db: Session, account_id: str, *, is_test: bool, actor: str) -> Account:
    """Staff-only mutator for Account.is_test - previously this flag had no
    route/service function anywhere (only settable via a direct DB update),
    even though it's the one thing that lets an account through the
    CONTROLLED_BETA/INTERNAL_TEST market-activation gate (see
    numbering.numbers.service._assert_market_activated) without a real
    legal sign-off. Real customer accounts should never get this flag -
    it's for internal/QA testing only."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")
    previous = account.is_test
    account.is_test = is_test
    db.commit()
    db.refresh(account)
    log_event(
        db, actor=actor, action="account.test_flag_changed", target=f"account:{account.id}",
        before={"is_test": previous}, after={"is_test": is_test},
    )
    return account


class LegalHoldRequiresReferenceError(Exception):
    """Raised when activating a legal hold with no reference - a real
    case/matter reference is required (same "record a reference, not just
    a reason" discipline as the market-activation legal sign-off), not a
    free-text justification."""


def set_account_legal_hold(
    db: Session, account_id: str, *, on: bool, reference: str | None, actor: str
) -> Account:
    """Architecture doc §10 "legal hold model for business customers" -
    while active, app.retention.service's purge sweeps skip every
    recording/voicemail on this account regardless of how overdue its
    normal retention window is. Staff-only (same SUPER_ADMIN bar as the
    test-flag toggle above) - this can override a customer's own configured
    retention preference, which is exactly the kind of override that needs
    a real audit trail, not a routine support action."""
    if on and not reference:
        raise LegalHoldRequiresReferenceError("Activating a legal hold requires a real case/matter reference")
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")
    previous = {"legal_hold": account.legal_hold, "legal_hold_reference": account.legal_hold_reference}
    account.legal_hold = on
    account.legal_hold_reference = reference if on else None
    db.commit()
    db.refresh(account)
    log_event(
        db, actor=actor, action="account.legal_hold_changed", target=f"account:{account.id}",
        before=previous, after={"legal_hold": account.legal_hold, "legal_hold_reference": account.legal_hold_reference},
    )
    return account


def list_stuck_provisioning(db: Session) -> list[dict]:
    """Staff recovery queue - numbers stranded mid-purchase by a process
    crash, joined with enough account context to act without a second
    lookup (same pattern as list_accounts_overview / compliance's
    list_all_cases / porting's list_all_porting_requests)."""
    numbers = _list_stuck_provisioning(db)
    account_ids = {n.account_id for n in numbers}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.account_id.in_(account_ids), User.role == UserRole.OWNER).all()
    }
    return [
        {
            "id": n.id,
            "e164": n.e164,
            "country": n.country,
            "status": n.status,
            "account_id": n.account_id,
            "account_name": accounts[n.account_id].name if n.account_id in accounts else None,
            "account_owner_email": owners.get(n.account_id),
            "provisioning_started_at": n.provisioning_started_at,
            "last_provisioning_error_code": n.last_provisioning_error_code,
            "provisioning_attempt_count": n.provisioning_attempt_count,
        }
        for n in numbers
    ]


def list_due_renewals(db: Session) -> list[dict]:
    """Staff worklist of numbers past their renewal date - see
    app.numbering.numbers.service.list_due_renewals's docstring on why
    this is a manual worklist rather than automated billing. Same
    join-in-context shape as list_stuck_provisioning."""
    numbers = _list_due_renewals(db)
    account_ids = {n.account_id for n in numbers}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.account_id.in_(account_ids), User.role == UserRole.OWNER).all()
    }
    return [
        {
            "id": n.id,
            "e164": n.e164,
            "country": n.country,
            "status": n.status,
            "account_id": n.account_id,
            "account_name": accounts[n.account_id].name if n.account_id in accounts else None,
            "account_owner_email": owners.get(n.account_id),
            "next_renewal_at": n.next_renewal_at,
        }
        for n in numbers
    ]


def list_access_matrix(db: Session) -> list[dict]:
    """The role x capability grid, grouped by capability - one row per
    capability with every role currently granted it (see
    app.staff.models.StaffCapabilityGrant's docstring). Grouped in Python
    rather than SQL since the grid is small (a dozen or so capabilities)
    and grouping here keeps the response shape simple for the staff
    console table."""
    grants = db.query(StaffCapabilityGrant).order_by(StaffCapabilityGrant.capability).all()
    by_capability: dict[str, list[str]] = {}
    for grant in grants:
        by_capability.setdefault(grant.capability, []).append(grant.role.value)
    return [
        {"capability": capability, "roles": sorted(roles)}
        for capability, roles in sorted(by_capability.items())
    ]


def grant_capability(db: Session, *, capability: str, role: PlatformStaffRole, actor: str) -> None:
    """Idempotent - granting a role that already has this capability is a
    no-op, not a duplicate-row error."""
    existing = (
        db.query(StaffCapabilityGrant)
        .filter(StaffCapabilityGrant.capability == capability, StaffCapabilityGrant.role == role)
        .first()
    )
    if existing is not None:
        return

    db.add(StaffCapabilityGrant(capability=capability, role=role))
    db.commit()
    log_event(
        db, actor=actor, action="staff.capability_granted", target=f"capability:{capability}",
        after={"role": role.value},
    )


def revoke_capability(db: Session, *, capability: str, role: PlatformStaffRole, actor: str) -> None:
    """Idempotent - revoking a grant that doesn't exist is a no-op.
    Refuses to remove the last remaining grant of
    MATRIX_MANAGEMENT_CAPABILITY (see LastGrantRemovalError's docstring) -
    every other capability, including one this exact staff member relies
    on for something else, can be revoked down to zero roles."""
    grant = (
        db.query(StaffCapabilityGrant)
        .filter(StaffCapabilityGrant.capability == capability, StaffCapabilityGrant.role == role)
        .first()
    )
    if grant is None:
        return

    if capability == MATRIX_MANAGEMENT_CAPABILITY:
        remaining = (
            db.query(StaffCapabilityGrant)
            .filter(StaffCapabilityGrant.capability == capability, StaffCapabilityGrant.id != grant.id)
            .count()
        )
        if remaining == 0:
            raise LastGrantRemovalError(
                f"Cannot revoke the last role able to manage the capability matrix ({role.value})"
            )

    db.delete(grant)
    db.commit()
    log_event(
        db, actor=actor, action="staff.capability_revoked", target=f"capability:{capability}",
        before={"role": role.value},
    )


def search_numbers(db: Session, query: str) -> list[dict]:
    """Cross-account number lookup for ops - a support agent has a number or
    a Twilio SID a customer gave them and needs to find which account owns
    it, without a database console. Same join-in-context shape as the other
    staff list views. Capped at 50 - this is a lookup tool for a specific
    number, not a bulk export."""
    pattern = f"%{query}%"
    numbers = (
        db.query(PhoneNumber)
        .filter(or_(PhoneNumber.e164.ilike(pattern), PhoneNumber.provider_sid.ilike(pattern)))
        .order_by(PhoneNumber.created_at.desc())
        .limit(50)
        .all()
    )
    account_ids = {n.account_id for n in numbers}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.account_id.in_(account_ids), User.role == UserRole.OWNER).all()
    }
    return [
        {
            "id": n.id,
            "e164": n.e164,
            "country": n.country,
            "status": n.status,
            "provider_sid": n.provider_sid,
            "account_id": n.account_id,
            "account_name": accounts[n.account_id].name if n.account_id in accounts else None,
            "account_owner_email": owners.get(n.account_id),
            "created_at": n.created_at,
        }
        for n in numbers
    ]
