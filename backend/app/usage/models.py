from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid

# Sentinel country_band used for a rate that applies to any country without
# its own row - see CallingRate/get_calling_rate. Not a real ISO code, so it
# can never collide with an actual country.
DEFAULT_RATE_COUNTRY = "XX"


class CallingRate(Base):
    """Per-country outbound calling price, keyed by the calling number's own
    country (PhoneNumber.country / UsageEvent.country_band) - not the call's
    destination. Destination-based international rating would need a real
    E.164-to-country parser (a new dependency this platform doesn't have),
    so this rates by which of the account's own numbers placed the call,
    the same dimension country_band already captures for billing. A row
    with country=DEFAULT_RATE_COUNTRY is the fallback for any curated
    country without its own explicit rate.

    Placeholder pricing, not a real carrier rate card - there's no live
    telecom billing gateway yet (see purchase_number's docstring on the
    matching gap for number purchase). This only prices UsageEvent rows for
    visibility; it does not charge anyone."""

    __tablename__ = "calling_rates"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    country: Mapped[str] = mapped_column(String(2), unique=True, nullable=False, index=True)
    price_per_minute_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(Base):
    """Rating input for a future billing integration (Architecture doc §7
    data model: "Usage Event... rating input for ZoikoNex"; Roadmap §2 lists
    "usage metering" as Phase 1 Voice scope in its own right, not contingent
    on ZoikoNex existing). Zoiko Local owns real-time usage capture; nothing
    downstream (ZoikoNex) exists yet to consume it, but the capture itself
    doesn't need to wait on that integration.

    idempotency_key prevents double-counting when a provider webhook (e.g.
    Twilio's status-callback) fires more than once for the same event.
    """

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    account_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    quantity: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    unit: Mapped[str] = mapped_column(String(20), nullable=False)
    country_band: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Priced from CallingRate at write time for "call_seconds" events only -
    # NULL for every other event_type (video minutes, AI summaries, etc.),
    # which have no rate table yet. An estimate for visibility, not a real
    # charge - see CallingRate's docstring on the same gap.
    estimated_cost_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
