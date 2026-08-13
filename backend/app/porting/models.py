import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class PortingRequestStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    COMPLETED = "completed"
    CANCELED = "canceled"


class PortingRequest(Base):
    """Roadmap/marketing-site "Number Porting" capability. Scoped
    deliberately: Twilio's real Porting API needs a signed Letter of
    Authorization and real losing-carrier account credentials - neither
    testable nor safely automatable without a real number to port, so the
    actual hand-off to the carrier is a manual step a staff member performs
    themselves outside this system. This model tracks the request
    end-to-end; twilio_incoming_number_sid and created_number_id are filled
    in by a human confirming a real port finished, not by an API call.
    """

    __tablename__ = "porting_requests"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_by_user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    phone_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    current_carrier: Mapped[str] = mapped_column(String(255), nullable=False)
    carrier_account_number: Mapped[str] = mapped_column(String(100), nullable=False)
    billing_name: Mapped[str] = mapped_column(String(255), nullable=False)
    billing_address: Mapped[str] = mapped_column(Text, nullable=False)
    # Commercial Billing Operating Standard doc §I1 - "port_case stores
    # authorization... evidence." An object-storage reference (see
    # app.integrations.storage - reused, not a new upload pipeline) to a
    # customer-supplied LOA-equivalent document. Optional: this codebase's
    # port-in is still a staff-mediated manual hand-off (see this class's
    # own docstring above), so submission isn't blocked on it, but staff
    # reviewing the request can now see it was actually provided.
    authorization_evidence_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # §I1 "target date" - customer-requested completion date, recorded but
    # not enforced against a real carrier (there's no real port-out
    # automation to enforce it against - see this class's docstring).
    target_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[PortingRequestStatus] = mapped_column(
        Enum(PortingRequestStatus, name="porting_request_status_enum"),
        nullable=False,
        default=PortingRequestStatus.SUBMITTED,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    twilio_incoming_number_sid: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_number_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
