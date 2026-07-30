from sqlalchemy.orm import Session

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
    return user


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        return None
    return user
