from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.security import hash_password, verify_password
from app.numbering.identity.models import Account, AccountType, User, UserRole


def create_account_with_owner(
    db: Session, account_name: str, account_type: str, email: str, password: str
) -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError("An account with this email already exists")

    account = Account(name=account_name, account_type=AccountType(account_type))
    db.add(account)
    db.flush()  # assigns account.id without committing yet

    user = User(
        account_id=account.id,
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.OWNER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_event(
        db,
        actor=user.id,
        action="account.signup",
        target=f"account:{account.id}",
        after={"account_id": account.id, "user_id": user.id, "email": user.email, "role": user.role},
    )
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    # Google-only accounts have no password at all - never match, and
    # never pass None into verify_password (it expects a real hash string).
    if not user or user.hashed_password is None or not verify_password(password, user.hashed_password):
        return None

    log_event(db, actor=user.id, action="user.login", target=f"user:{user.id}")
    return user


def find_or_create_user_from_google(db: Session, email: str, name: str | None) -> User:
    """Logs in an existing account by email, or creates a brand new
    individual account + Google-only User (no password) if none exists."""
    user = db.query(User).filter(User.email == email).first()
    if user:
        log_event(db, actor=user.id, action="user.login", target=f"user:{user.id}", reason="google")
        return user

    account = Account(name=name or email, account_type=AccountType.INDIVIDUAL)
    db.add(account)
    db.flush()

    user = User(
        account_id=account.id,
        email=email,
        hashed_password=None,
        role=UserRole.OWNER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_event(
        db,
        actor=user.id,
        action="account.signup",
        target=f"account:{account.id}",
        reason="google",
        after={"account_id": account.id, "user_id": user.id, "email": user.email, "role": user.role},
    )
    return user
