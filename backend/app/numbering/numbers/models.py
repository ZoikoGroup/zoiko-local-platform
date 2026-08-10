import enum
import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class PhoneNumberStatus(str, enum.Enum):
    RESERVED = "reserved"
    COMPLIANCE_PENDING = "compliance_pending"
    PURCHASE_PENDING = "purchase_pending"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    e164: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="twilio")
    provider_sid: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    status: Mapped[PhoneNumberStatus] = mapped_column(
        Enum(PhoneNumberStatus, name="phone_number_status_enum"), nullable=False
    )
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Roadmap §6 number lifecycle - "Quarantine period before reuse, default
    # 90 days." Set when the number moves to CANCELLED; checked by
    # reserve_number() before letting anyone (including the same account)
    # grab it again.
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Architecture doc's "Provisioning Job" object, in miniature - set when
    # status enters PURCHASE_PENDING, cleared the moment it resolves to
    # ACTIVE or back to RESERVED. The normal purchase_number() flow never
    # returns to the caller with this still set - a non-null value on a row
    # still sitting in PURCHASE_PENDING/PROVISIONING means the process died
    # mid-purchase, which is exactly what the staff recovery queue
    # (app/staff/service.py's list_stuck_provisioning) surfaces.
    provisioning_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Roadmap "Team and RBAC ... number assignment" — which team member this
    # number is handed to (e.g. a sales line given to one agent). NULL means
    # unassigned: any Owner/Admin on the account can still manage it, but no
    # plain Member can until an Owner/Admin assigns it to them.
    assigned_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Basic routing (Roadmap §2 "Voice: ... call forwarding ... business-hours
    # routing"). No forwarding_number = always go to voicemail. A forwarding
    # number with no business hours set = always forward.
    forwarding_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    business_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    business_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    business_hours_timezone: Mapped[str] = mapped_column(String(50), nullable=False, default="UTC")

    # Roadmap §7 "AI Receptionist" — guarded caller-qualification flow when
    # no forwarding_number applies (or outside business hours). Off by
    # default: a number with neither forwarding nor this enabled just goes
    # straight to voicemail, same as before this feature existed.
    ai_receptionist_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Roadmap §7 "Routing: Escalate to nominated team member" — the specific
    # team member urgent receptionist calls are escalated to, distinct from
    # forwarding_number (which is also used for plain business-hours call
    # forwarding, unrelated to receptionist urgency). NULL means no one is
    # nominated, so urgent calls fall back to the polite-close/voicemail
    # branch even if forwarding_number happens to be set for other reasons.
    escalation_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    # Architecture doc Phase 2 "enhanced business routing" - IVR-style menu
    # ("press 1 for sales, 2 for support"). Non-null/non-empty means the
    # number has an IVR menu configured (see IVROption below) and inbound
    # calls play this greeting and gather a digit before falling into the
    # existing ring-group/receptionist/voicemail flow. A number with no
    # greeting set behaves exactly as before this feature existed.
    ivr_greeting: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Number lifecycle "renewal" date - set to purchase time + the renewal
    # period whenever the number becomes ACTIVE (initial purchase or a
    # staff-recovered stuck purchase), advanced by the same period each
    # time app.numbering.numbers.service.mark_number_renewed runs. NULL for
    # numbers that have never been ACTIVE (reserved/cancelled/pending).
    # There's no real payment gateway yet (see purchase_number's docstring
    # on that same gap), so this only tracks the date and gives staff a
    # manual bookkeeping action - it does not enforce anything or suspend
    # numbers on its own.
    next_renewal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Phase 3 "Advanced IVR builder" (Call Flow Designer) - when set, an
    # inbound call is routed through this flow's *live* version instead of
    # the plain forwarding_number/ai_receptionist_enabled/ring-group logic
    # above. NULL (the default for every existing number) preserves the
    # exact pre-existing behavior untouched - same non-breaking pattern as
    # ring_group_destinations.
    call_flow_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("call_flows.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Phase 3 "WhatsApp Business integration" - real WhatsApp Business
    # senders are approved per-number by Meta/Twilio out-of-band (not
    # something this app can provision itself); this flag just records that
    # approval has happened, gating app.messaging.service.send_message the
    # same way ai_receptionist_enabled gates the receptionist above. Off by
    # default, so no existing number is affected.
    whatsapp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Phase 3 "SMS by regulated market" - gates app.messaging.service the
    # same way whatsapp_enabled does. Real US business SMS additionally
    # requires A2P 10DLC brand/campaign registration with the carrier
    # (architecture doc: "Separate regulated workstream"), which happens
    # out-of-band; this flag just records that it's done for this number.
    sms_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IVROption(Base):
    """One keypad choice on a number's IVR menu (see PhoneNumber.ivr_greeting).
    Pressing `digit` rings `destination_number` (single dial, same overflow-
    to-voicemail fallback as the ring group / plain forwarding paths - see
    voice.py's /ivr-select route). A digit with no matching option, or no
    input before the gather times out, falls through to the number's
    existing ring-group/receptionist/voicemail behavior."""

    __tablename__ = "ivr_options"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    digit: Mapped[str] = mapped_column(String(1), nullable=False)
    destination_number: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RingGroupDestination(Base):
    """Architecture doc Phase 2 "enhanced business routing" - additive to
    forwarding_number, not a replacement: when a number has rows here,
    inbound forwarded calls ring all of them simultaneously (a Twilio
    <Dial> with multiple <Number> children - first to answer wins, the
    rest stop ringing) instead of the single forwarding_number. A number
    with zero rows here behaves exactly as before this feature existed.
    """

    __tablename__ = "ring_group_destinations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    destination_number: Mapped[str] = mapped_column(String(20), nullable=False)
    ring_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
