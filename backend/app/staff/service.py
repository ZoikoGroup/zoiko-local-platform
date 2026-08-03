from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.numbering.identity.models import Account, User, UserRole
from app.numbering.numbers.models import PhoneNumber
from app.staff.models import PlatformStaff, PlatformStaffRole


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
