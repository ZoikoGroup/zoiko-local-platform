from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class Contact(Base):
    """Account-wide address book (Roadmap-adjacent, not in the original spec
    docs - built to replace the frontend's "Contacts" stub, which promised
    "saved contacts and call/message history with each one"). Shared across
    the whole account like phone numbers, not private per-user - any team
    member can see and use the account's contacts, matching how Calls/
    Numbers already work. phone_number is intentionally NOT unique - the
    same external number could legitimately be saved twice under different
    names by mistake, and it's not worth blocking that at the DB level.
    """

    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
