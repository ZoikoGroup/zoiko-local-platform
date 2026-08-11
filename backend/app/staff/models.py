import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class PlatformStaffRole(str, enum.Enum):
    """Segregation of duties within the ops console (Architecture doc
    §11/§10: "segregation of duties for sensitive actions"). SUPPORT is
    read-only (accounts/cases/audit lookups for customer support); only
    COMPLIANCE_OFFICER and SUPER_ADMIN can approve/reject KYC cases."""

    SUPPORT = "support"
    COMPLIANCE_OFFICER = "compliance_officer"
    SUPER_ADMIN = "super_admin"


class PlatformStaff(Base):
    """A Zoiko employee (ops/compliance reviewer), not a customer.

    Deliberately separate from numbering.identity.User - staff don't
    belong to any customer Account, and there is no public signup
    endpoint for this table. Accounts are provisioned internally only
    (see app/seed.py), matching the docs' "Admin Operations: Internal
    console" - this is not a customer-facing role.
    """

    __tablename__ = "platform_staff"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[PlatformStaffRole] = mapped_column(
        Enum(PlatformStaffRole, name="platform_staff_role_enum"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StaffCapabilityGrant(Base):
    """Architecture doc §10/§11 "segregation of duties for sensitive
    actions," formalized as a queryable role x capability grid (the
    Commercial Billing Operating Standard doc's explicit ask for a "formal
    RBAC/segregation-of-duties matrix") instead of PlatformStaffRole
    literals scattered as arguments across route files. Each row grants
    one role permission to perform one named capability;
    app.core.deps.require_capability looks this table up at request time -
    the grid IS the authorization source of truth now, not the route code.
    Seeded via migration to exactly match this codebase's pre-existing
    require_staff_role(...) call sites (see that migration's docstring for
    the full grid) - changing who can do what now means editing data, not
    redeploying code."""

    __tablename__ = "staff_capability_grants"
    __table_args__ = (UniqueConstraint("capability", "role", name="uq_staff_capability_grant"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    capability: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    role: Mapped[PlatformStaffRole] = mapped_column(
        Enum(PlatformStaffRole, name="platform_staff_role_enum"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
