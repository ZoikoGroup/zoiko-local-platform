from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.numbering.identity.models import User, UserRole
from app.staff.models import PlatformStaff

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """A logged-in customer account user (signup/login). Rejects staff
    tokens - a staff login can never be used as if it were a customer."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None or payload.get("scope") != "customer":
        raise credentials_error

    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is None:
        raise credentials_error

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """A customer account Owner/Admin - scoped to THEIR OWN account only.
    Do not use this for anything that should be reviewed independently
    of the customer (e.g. KYC approval) - use get_current_staff instead."""
    if current_user.role not in (UserRole.OWNER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required",
        )
    return current_user


def get_current_staff(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """A logged-in Zoiko platform staff member. Rejects customer tokens -
    no customer, including an account Owner, can act as staff."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None or payload.get("scope") != "staff":
        raise credentials_error

    staff = db.query(PlatformStaff).filter(PlatformStaff.id == payload.get("sub")).first()
    if staff is None or not staff.is_active:
        raise credentials_error

    return staff
