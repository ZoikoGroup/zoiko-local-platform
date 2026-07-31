from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.staff.models import PlatformStaff


def create_staff(db: Session, email: str, password: str) -> PlatformStaff:
    existing = db.query(PlatformStaff).filter(PlatformStaff.email == email).first()
    if existing:
        raise ValueError("A staff account with this email already exists")

    staff = PlatformStaff(email=email, hashed_password=hash_password(password))
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def authenticate_staff(db: Session, email: str, password: str) -> PlatformStaff | None:
    staff = db.query(PlatformStaff).filter(PlatformStaff.email == email).first()
    if not staff or not staff.is_active or not verify_password(password, staff.hashed_password):
        return None
    return staff
