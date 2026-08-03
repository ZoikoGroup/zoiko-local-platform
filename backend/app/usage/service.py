from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.usage.models import UsageEvent


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
    return event


def list_account_usage(db: Session, account_id: str) -> list[UsageEvent]:
    return (
        db.query(UsageEvent)
        .filter(UsageEvent.account_id == account_id)
        .order_by(UsageEvent.created_at.desc())
        .all()
    )
