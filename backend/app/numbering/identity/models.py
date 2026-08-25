import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.ids import new_uuid
from app.risk.models import AccountRiskState


class AccountType(str, enum.Enum):
    INDIVIDUAL = "individual"
    BUSINESS = "business"


class AccountBillingClassification(str, enum.Enum):
    """Commercial Billing Operating Standard doc §5 P0 blocker + Table 8's
    canonical 9-value grid - which of these an account is decides whether
    it may ever create a real charge at all, independent of anything else
    (quota, entitlement, KYC). COM-03: "non-commercial classes cannot
    create live customer charges." Every account gets one; there is no
    "unclassified" state."""

    COMMERCIAL_STANDALONE = "commercial_standalone"
    COMMERCIAL_BUNDLED = "commercial_bundled"
    LEGACY_MIGRATION = "legacy_migration"
    PILOT_NON_BILLABLE = "pilot_non_billable"
    PARTNER_SPONSORED = "partner_sponsored"
    INTERNAL = "internal"
    DEMO = "demo"
    SANDBOX = "sandbox"
    QA_AUTOMATION = "qa_automation"


class AccountBillingSource(str, enum.Enum):
    """Doc §19 O3: "Each entitlement period resolves to one billing
    source" - which system actually charges/entitles this account, so a
    bundle or partner deal can never silently double-charge alongside a
    direct Zoiko Local charge for the same period."""

    DIRECT_ZOIKO_LOCAL = "direct_zoiko_local"
    ZOIKO_ONE_BUNDLE = "zoiko_one_bundle"
    PARTNER = "partner"
    LEGACY = "legacy"


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
    # Narrow, separate stopgap from billing_classification/billing_source
    # below: a single boolean that blocks the real-money boundaries (Stripe
    # checkout, run_billing_cycle, credit/debit notes, refunds) for an
    # account flagged as synthetic/test - predates the full classification
    # enum and still used independently by its own call sites (see
    # app.numbering.numbers.service and app.billing.service).
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Every account gets a real classification/source at creation -
    # COMMERCIAL_STANDALONE/DIRECT_ZOIKO_LOCAL for the normal public
    # signup path (see create_account_with_owner). Non-default values are
    # set later by staff (e.g. marking an account DEMO or SANDBOX) - there
    # is no public signup path for any class other than the default.
    billing_classification: Mapped[AccountBillingClassification] = mapped_column(
        Enum(AccountBillingClassification, name="account_billing_classification_enum"),
        nullable=False, default=AccountBillingClassification.COMMERCIAL_STANDALONE,
    )
    billing_source: Mapped[AccountBillingSource] = mapped_column(
        Enum(AccountBillingSource, name="account_billing_source_enum"),
        nullable=False, default=AccountBillingSource.DIRECT_ZOIKO_LOCAL,
    )
    # Production Readiness Standard doc's "trial-abuse step-up model" - see
    # AccountRiskState's docstring. Every new account starts at the tightest
    # tier (fail-closed, same posture as MarketActivationStatus.CLOSED) -
    # existing accounts are backfilled to PAID_NORMAL by this column's
    # migration to preserve today's de facto unrestricted behavior.
    risk_state: Mapped[AccountRiskState] = mapped_column(
        Enum(AccountRiskState, name="account_risk_state_enum"),
        nullable=False, default=AccountRiskState.TRIAL_LOW,
    )
    # Legal/litigation hold - set by staff (accounts.manage_legal_hold
    # capability, SUPER_ADMIN only, migration c3b0f40f4bc1) whenever this
    # account's data must not be destroyed pending a legal matter. Enforced
    # at the one place data destruction is actually requested through this
    # app: app.retention.service.create_erasure_request/resolve_erasure_
    # request refuse to erasure-request (or complete an erasure of) an
    # account while this is True. Independent of is_test above - a legal
    # hold is a litigation/investigation concern, not a billing one.
    legal_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Free-text reference to the matter/case this hold is tied to (e.g. a
    # legal case number) - nullable since a hold can be placed before a
    # formal reference exists yet.
    legal_hold_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
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
