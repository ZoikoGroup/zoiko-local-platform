import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class BlockedDestination(Base):
    """Platform-wide outbound-dialing blocklist (Architecture doc §5 "Fraud
    and Risk": "provider blacklists"; §13 Commercial: "blocked destinations").

    A rule as data, not a hardcoded if-statement (same "compliance as code"
    doctrine the ComplianceRule table follows) - staff-managed, checked
    against every outbound call regardless of account. `prefix` matches an
    E.164 number by startswith, so a rule can block a whole country code
    (e.g. "+234") or a narrower premium-rate range (e.g. "+1900").
    """

    __tablename__ = "blocked_destinations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    prefix: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RiskSignalType(str, enum.Enum):
    VELOCITY_EXCEEDED = "velocity_exceeded"
    BLOCKED_DESTINATION_ATTEMPT = "blocked_destination_attempt"
    # International Revenue Share Fraud (IRSF) pattern: a compromised or
    # abused account suddenly dials many different countries in a short
    # window, unlike a legitimate cross-border business's steadier spread -
    # see assert_geographic_dispersion_ok.
    GEOGRAPHIC_DISPERSION = "geographic_dispersion"
    # Commercial Billing Operating Standard doc's "real-time fraud/toll-
    # abuse spend controls" - a compromised account racking up outbound
    # call cost far faster than normal, independent of call COUNT
    # (velocity) or destination (geographic dispersion) - a sustained
    # string of calls to one expensive premium-rate destination would
    # trip this without necessarily tripping either of those. See
    # assert_spend_limit_ok.
    SPEND_LIMIT_EXCEEDED = "spend_limit_exceeded"
    # Architecture doc §5 "Fraud and Risk: device fingerprinting" - the
    # same browser/device signing up (or logging into) many distinct
    # accounts in a short window, the classic free-trial/quota abuse
    # pattern. Detection only (never blocks signup/login itself - a
    # coarse client-side fingerprint has real false-positive risk, e.g.
    # a shared office network or a family device) - see
    # is_suspected_fingerprint_abuse.
    DEVICE_FINGERPRINT_ABUSE = "device_fingerprint_abuse"


class RiskSignal(Base):
    """One occurrence of a fraud/abuse rule actually firing against an
    account (Roadmap doc §13 Risk Register: "anomalous usage", "account risk
    scoring"). `assert_destination_allowed`/`assert_outbound_velocity_ok`
    already blocked the call in the moment by raising - this table is the
    evidence trail of *how often* that's happened per account, which is what
    a risk score and an auto-suspend decision need to work from. Without it,
    a repeatedly-abusive account looks identical to one that tripped a rule
    once, since the block itself leaves no queryable row anywhere but the
    audit log's free-text fields.
    """

    __tablename__ = "risk_signals"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # No values_callable override here (unlike an earlier draft of this
    # column that briefly assumed lowercase storage) - the actual live
    # risksignaltype Postgres type was fixed to this codebase's usual
    # uppercase-.name convention (see the a80b7b11ce8e migration's fix
    # this session for why: it matches call_direction_enum and every other
    # enum column here), so SQLAlchemy's default Enum(SomeStrEnum) behavior
    # (send the member's .name) is exactly correct.
    signal_type: Mapped[RiskSignalType] = mapped_column(Enum(RiskSignalType), nullable=False)
    detail: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class FraudRule(Base):
    """Per-signal-type scoring weight, as data rather than a hardcoded
    Python dict - same "rules as data" doctrine ComplianceRule already
    follows in this codebase. Lets staff retune the fraud model (or turn a
    noisy signal off entirely) without a code deploy. A signal type with no
    active row here falls back to a conservative built-in default - see
    service.py's _DEFAULT_WEIGHTS.
    """

    __tablename__ = "fraud_rules"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    # No values_callable override - see RiskSignal.signal_type's docstring
    # above for why the plain default (.name, uppercase) is correct here.
    signal_type: Mapped[RiskSignalType] = mapped_column(
        Enum(RiskSignalType), unique=True, nullable=False,
    )
    weight: Mapped[int] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FraudCaseStatus(str, enum.Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    CLEARED = "cleared"


class DeviceFingerprintSighting(Base):
    """Architecture doc §5 "Fraud and Risk: device fingerprinting" - one
    row per signup/login where the client sent a fingerprint hash (see
    frontend's coarse client-side fingerprint - navigator/screen/timezone,
    no third-party fingerprinting SDK). Deliberately just a sightings log,
    not a dedup/identity table - the fraud signal is "how many distinct
    accounts has this fingerprint touched recently," computed from this
    log by is_suspected_fingerprint_abuse, the same shape as
    is_suspected_spam_caller's use of CallRecord."""

    __tablename__ = "device_fingerprint_sightings"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    fingerprint_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class FraudCase(Base):
    """Human-in-the-loop review queue, opened when an account's decayed
    risk score crosses REVIEW_THRESHOLD but hasn't (yet) reached
    AUTO_SUSPEND_THRESHOLD - the gap the old binary "score >= 100 -> instant
    suspend, otherwise nothing visible" design left: real fraud ops tooling
    surfaces a rising-risk account to a human before it's severe enough to
    auto-suspend, not only after. Auto-suspension at the higher threshold
    still happens immediately regardless of this queue - this is the
    earlier-warning tier, not a replacement for it.
    """

    __tablename__ = "fraud_cases"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score_at_open: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[FraudCaseStatus] = mapped_column(
        Enum(FraudCaseStatus), nullable=False, default=FraudCaseStatus.OPEN
    )
    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolution_notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
