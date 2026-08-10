from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.numbering.identity.models import Account, User, UserRole
from app.numbering.numbers.models import PhoneNumber
from app.numbering.numbers.service import list_due_renewals as _list_due_renewals
from app.numbering.numbers.service import list_stuck_provisioning as _list_stuck_provisioning
from app.staff.models import PlatformStaff, PlatformStaffRole, StaffCapabilityGrant


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
            "created_at": account.created_at,
        }
        for account in accounts
    ]


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
