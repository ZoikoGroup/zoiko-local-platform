from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.billing.service import sync_usage_event_to_zoikonex
from app.events.service import publish_usage_rated
from app.usage.models import DEFAULT_RATE_COUNTRY, CallingRate, UsageEvent


def get_calling_rate(db: Session, country: str | None) -> CallingRate | None:
    """country-specific rate if one's configured, else the DEFAULT_RATE_
    COUNTRY fallback, else None (no rate configured at all yet)."""
    if country is not None:
        rate = db.query(CallingRate).filter(CallingRate.country == country).first()
        if rate is not None:
            return rate
    return db.query(CallingRate).filter(CallingRate.country == DEFAULT_RATE_COUNTRY).first()


def list_calling_rates(db: Session) -> list[CallingRate]:
    return db.query(CallingRate).order_by(CallingRate.country.asc()).all()


def upsert_calling_rate(db: Session, *, country: str, price_per_minute_cents: int, currency: str = "USD") -> CallingRate:
    rate = db.query(CallingRate).filter(CallingRate.country == country).first()
    if rate is None:
        rate = CallingRate(country=country, price_per_minute_cents=price_per_minute_cents, currency=currency)
        db.add(rate)
    else:
        rate.price_per_minute_cents = price_per_minute_cents
        rate.currency = currency
    db.commit()
    db.refresh(rate)
    return rate


def record_usage_event(
    db: Session,
    *,
    account_id: str,
    event_type: str,
    quantity: float,
    unit: str,
    country_band: str | None,
    idempotency_key: str,
) -> UsageEvent | None:
    """Returns None (no-op) if this exact event was already recorded - a
    provider webhook firing twice for the same call must not double-count
    usage."""
    existing = db.query(UsageEvent).filter(UsageEvent.idempotency_key == idempotency_key).first()
    if existing is not None:
        return None

    # estimated_cost_cents starts NULL - usage capture must never be
    # blocked on rating, so the actual $ decision is made afterward by
    # ZoikoNex (see sync_usage_event_to_zoikonex/zoikonex_adapter.
    # rate_usage_event) rather than computed here ad hoc.
    event = UsageEvent(
        account_id=account_id,
        event_type=event_type,
        quantity=quantity,
        unit=unit,
        country_band=country_band,
        idempotency_key=idempotency_key,
    )
    db.add(event)
    try:
        db.commit()
    except IntegrityError:
        # lost the race against a concurrent duplicate webhook - the other
        # commit already recorded this event, so this one is correctly a no-op
        db.rollback()
        return None
    db.refresh(event)
    publish_usage_rated(
        account_id, usage_event_id=event.id, event_type=event_type, quantity=quantity,
        unit=unit, country_band=country_band,
    )
    sync_usage_event_to_zoikonex(db, event)
    return event


def list_account_usage(db: Session, account_id: str) -> list[UsageEvent]:
    return (
        db.query(UsageEvent)
        .filter(UsageEvent.account_id == account_id)
        .order_by(UsageEvent.created_at.desc())
        .all()
    )
