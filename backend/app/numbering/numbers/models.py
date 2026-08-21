import enum
import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, JSON, String, Time, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def new_uuid() -> str:
    return str(uuid.uuid4())


class MarketActivationStatus(str, enum.Enum):
    """Production Readiness Standard doc §6.2 "Market Activation Registry" -
    a graduated rollout state per country, replacing the old binary "is it
    in SupportedCountry or not" model. "Provider has numbers there" and
    "Zoiko intends to operate there" are explicitly NOT sufficient
    activation criteria per that doc - a country can now move through
    internal testing and invite-only beta before real commercial sale,
    and be pulled instantly without deleting its SupportedCountry row
    (which would also wipe its emergency_calling_supported/eligibility
    history)."""

    CLOSED = "closed"
    INTERNAL_TEST = "internal_test"
    CONTROLLED_BETA = "controlled_beta"
    PAID_OPEN = "paid_open"
    SUSPENDED = "suspended"


class SupportedCountry(Base):
    """Zoiko Local's curated launch-country list (Architecture doc's
    "6-8 priority countries", not "whatever Twilio happens to expose").
    Stored as data, not a hardcoded Python list - the Commercial Billing
    Operating Standard doc (§19) names a hardcoded country-availability
    list as a P0 launch blocker, the same rule it applies to plan names
    and prices (see app.usage.models.CallingRate for the pricing side of
    the same discipline). Staff-managed via PUT /staff/countries
    (SUPER_ADMIN only, same bar as calling-rate changes)."""

    __tablename__ = "supported_countries"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    code: Mapped[str] = mapped_column(String(2), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Commercial Billing Operating Standard doc §10/§S1/§34 - "a number/
    # calling service cannot be marketed as emergency-capable unless the
    # applicable configuration and evidence are approved." Defaults False
    # (matches reality: no market here has verified E911 evidence/routing -
    # see EmergencyDisclosureRequiredError's docstring). This is a
    # disclosure-accuracy flag, not a claim of real E911 capability; flip it
    # only once real emergency-routing evidence for that country exists.
    emergency_calling_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Production Readiness Standard doc §6.2/§6.3 - defaults CLOSED for any
    # NEW country going forward (fail-closed: "provider has numbers there"
    # is not activation), matching Annex B's "Market availability is
    # policy-controlled and default-deny." The migration backfills this
    # project's existing pre-seeded countries to PAID_OPEN to preserve
    # their current behavior - an honest "already de facto sellable in
    # this dev/demo build" carry-forward, not a real Legal/Tax/Compliance
    # PAID_OPEN sign-off per that doc's §6.3 minimum market file, which
    # nothing in this codebase has been through yet.
    market_status: Mapped[MarketActivationStatus] = mapped_column(
        Enum(MarketActivationStatus, name="market_activation_status_enum"),
        nullable=False, default=MarketActivationStatus.CLOSED,
    )
    # Readiness doc §6.2: "PAID_OPEN only after legal/tax/telecom/privacy/
    # consumer review and named sign-off." Previously set_market_activation_
    # status only recorded a free-text `reason` on the audit log - anyone
    # with the capability could flip a country to PAID_OPEN with a reason
    # like "testing." These two columns are the actual named-reviewer
    # evidence the doc requires, enforced (non-null) specifically for a
    # transition INTO PAID_OPEN - see set_market_activation_status.
    # NULL on this project's 8 pre-existing PAID_OPEN countries (see the
    # comment above) since backfilling a fake reviewer name would be
    # exactly the kind of invented approval this doc prohibits - they
    # remain honestly unaudited until someone actually does that review.
    legal_signoff_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    legal_signoff_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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

    # Commercial Billing Operating Standard doc §7 "Number Inventory,
    # Eligibility, Reservation & Provisioning" - the search step already
    # accepts a number_type ("local", "toll_free", ...), but until now
    # nothing persisted which type was actually reserved. Needed so
    # NumberEligibilityRule (below) can key market/number-type-specific
    # requirements the same way ComplianceRule keys KYC requirements by
    # country - defaults to "local" (the only type this platform actually
    # sells end-to-end today) so every existing row is unaffected.
    number_type: Mapped[str] = mapped_column(String(30), nullable=False, default="local", server_default="local")

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


class CallerIdentityStatus(str, enum.Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    RESTRICTED = "restricted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class CallerIdentity(Base):
    """Commercial Billing Operating Standard doc §R6 'How is caller-ID
    spoofing risk controlled? ... Only verified/authorized caller IDs may
    be presented... caller_identity object records verification/
    authorization source, allowed presentation scope and status; routing
    rejects unauthorized combinations.' Data model line: 'verified CLI,
    account, verification/authorization source, provider/market scope,
    status, expiry'.

    Deliberately a separate object from PhoneNumber.status: ACTIVE+owned
    already prevents presenting a number nobody bought (see
    place_outbound_call's ownership check), but that's an ownership
    check, not a formal, auditable verification record with its own
    source/scope/expiry/revocation lifecycle - this is that record.
    Auto-created VERIFIED at the moment a number is genuinely provisioned
    (real Twilio purchase or completed port-in, both real
    verification/authorization sources on their own) - see
    purchase_number/app.porting.service.complete_porting_request. Never
    created UNVERIFIED-and-left-that-way for a number this platform
    actually provisioned; UNVERIFIED only means "no row exists yet",
    which assert_caller_id_authorized treats as not authorized.
    """

    __tablename__ = "caller_identities"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[CallerIdentityStatus] = mapped_column(
        Enum(CallerIdentityStatus, name="caller_identity_status_enum"),
        nullable=False, default=CallerIdentityStatus.UNVERIFIED,
    )
    verification_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # "Allowed presentation scope" (doc's phrasing) - which market/provider
    # this verification covers. "global" until real per-market STIR/SHAKEN
    # attestation or provider-specific scoping exists.
    scope: Mapped[str] = mapped_column(String(50), nullable=False, default="global")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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


class NumberEligibilityCaseStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class NumberEligibilityRule(Base):
    """Commercial Billing Operating Standard doc §7 C1/C3: "Implement a
    versioned market/release registry... covering availability, eligibility
    documents, geographic/address restrictions..." and "Each number binds
    to market_id, number_type, eligibility_profile... independently." Data-
    driven per (country, number_type), same "rules as data, never hardcoded
    if-statements" discipline as app.compliance.models.ComplianceRule - a
    combination with no ACTIVE row here needs no eligibility case at all -
    confirmed live in purchase_number: the gate triggers on the mere
    EXISTENCE of an active rule (is_active=True), regardless of whether
    required_evidence is empty, since opening a case still requires it to
    reach APPROVED before purchase proceeds. seed_market_release_registry
    therefore seeds every supported country with is_active=False - real,
    queryable reference data for the P0-2 registry fields below, with zero
    change to today's purchase behavior. A market's rows only start
    gating once staff explicitly flip is_active=True (with real
    required_evidence, once one is actually decided for that market).
    Kept separate from ComplianceRule because this gates a specific
    requested NUMBER (e.g. proof of local presence for one geographic-
    restricted number type in one country), not the account-level
    KYC/KYB identity ComplianceRule already covers.

    emergency_calling_supported/recording_supported/allowed_calling_directions
    (see their own column comments) are the P0-2 "market/release registry"
    fields - deliberately scoped to what this product actually supports,
    not an attempt to encode real per-country telecom/privacy law.
    """

    __tablename__ = "number_eligibility_rules"
    __table_args__ = (UniqueConstraint("country", "number_type", name="uq_number_eligibility_rule_country_type"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    country: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    number_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    required_evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Commercial Billing Operating Standard §7 P0-2: "emergency-service
    # capability, ... recording/transcription... inbound/outbound calling".
    # Deliberately scoped to what ZOIKO LOCAL'S OWN PRODUCT actually
    # supports in this market, not an attempt to encode each country's real
    # telecom/privacy law (that would mean inventing legal facts nobody has
    # reviewed - the same category of problem TEST_PLACEHOLDER_PRICES was
    # created to avoid for pricing). emergency_calling_supported=False is
    # simply true everywhere right now (no real E911/999 routing exists
    # anywhere in this codebase - see the emergency-disclosure consent gate
    # in app.compliance already required before every number purchase).
    # recording_supported/allowed_calling_directions reflect what's
    # actually built and already gated by the existing AI-processing/
    # recording consent flow, not a new legal claim.
    emergency_calling_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recording_supported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allowed_calling_directions: Mapped[str] = mapped_column(
        Enum("inbound_only", "outbound_only", "both", name="calling_direction_enum"),
        nullable=False, default="both",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NumberEligibilityCase(Base):
    """Commercial Billing Operating Standard doc §7 C3: "eligibility_case
    links requested number/market to evidence, reviewer/automation result,
    provider submission and expiry. Reject incomplete cases without
    charging recurring rental." One case per requested number (not per
    account - see NumberEligibilityRule's docstring for why this is
    distinct from app.compliance.models.ComplianceCase). Evidence here is
    lightweight structured metadata the customer/staff exchange, not a
    file-upload feature - extend to real document storage the same way
    ComplianceCase does (app.integrations.storage.s3) if/when a specific
    market's eligibility profile actually needs it; deliberately not built
    for this first pass since no active rule exists yet to need it.
    "Reject incomplete cases without charging recurring rental" is
    satisfied structurally: next_renewal_at is only ever set once a number
    reaches ACTIVE (see app.numbering.numbers.service.purchase_number),
    which can't happen while a case here is anything but APPROVED.
    """

    __tablename__ = "number_eligibility_cases"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    phone_number_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("phone_numbers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    number_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[NumberEligibilityCaseStatus] = mapped_column(
        Enum(NumberEligibilityCaseStatus, name="number_eligibility_case_status_enum"),
        nullable=False,
        default=NumberEligibilityCaseStatus.PENDING,
    )
    evidence: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    review_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
