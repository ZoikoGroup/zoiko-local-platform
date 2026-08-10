import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.ids import new_uuid


class AccountType(str, enum.Enum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"
    # Roadmap §2 Accounts: "Viewer/Auditor... Phase 1.5 unless required."
    # Full read access account-wide (unlike Member, who's restricted to
    # numbers assigned to them - every `!= UserRole.MEMBER` check
    # throughout this codebase already treats Viewer as unrestricted-read
    # for free), zero write access anywhere (enforced by
    # app.core.deps.require_writer on every write endpoint).
    VIEWER = "viewer"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type_enum"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="account", cascade="all, delete-orphan"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    # Nullable - only needed for the SMS notification channel; most users
    # will never set this, and email-only notifications work fine without it.
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Nullable: a Google-only account has no password to check - only ever
    # log in via /auth/google for that user, never /auth/login.
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role_enum"), nullable=False, default=UserRole.OWNER
    )
    # MFA (TOTP, e.g. Google Authenticator). mfa_secret is set as soon as
    # setup starts, but mfa_enabled only flips to True once the user
    # proves they can generate a valid code - see /auth/mfa/enable.
    mfa_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    account: Mapped["Account"] = relationship("Account", back_populates="users")
