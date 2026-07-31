from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


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
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
