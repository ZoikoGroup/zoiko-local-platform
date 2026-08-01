from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.integrations.telecom import twilio as telecom
from app.notifications.service import notify_number_activated
from app.numbering.identity.models import User, UserRole
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

RESERVATION_TTL_MINUTES = 12


class NumberConflictError(Exception):
    """Raised when a number can't be reserved/purchased because another
    account already holds it, or the caller's own reservation lapsed."""


def search_numbers(country: str, number_type: str = "local", area_code: str | None = None) -> list[dict]:
    return telecom.search_available_numbers(country, number_type=number_type, area_code=area_code)


def reserve_number(db: Session, account_id: str, e164: str, country: str) -> PhoneNumber:
    """Atomicity law: two accounts must never hold a live reservation on the
    same number. `SELECT ... FOR UPDATE` serializes concurrent reservers of an
    existing row; the unique constraint on `e164` catches the race where two
    requests both try to INSERT a brand-new row for the same number.
    """
    now = datetime.now(timezone.utc)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()

    if number is None:
        number = PhoneNumber(
            e164=e164,
            country=country,
            status=PhoneNumberStatus.RESERVED,
            account_id=account_id,
            reserved_until=now + timedelta(minutes=RESERVATION_TTL_MINUTES),
        )
        db.add(number)
    elif number.status == PhoneNumberStatus.RESERVED and number.account_id != account_id and (
        number.reserved_until is not None and number.reserved_until > now
    ):
        raise NumberConflictError(f"{e164} is already reserved by another account")
    elif number.status in (
        PhoneNumberStatus.PURCHASE_PENDING,
        PhoneNumberStatus.ACTIVE,
        PhoneNumberStatus.SUSPENDED,
    ):
        raise NumberConflictError(f"{e164} is not available")
    else:
        # own expired-or-active reservation, or a released/cancelled row: re-reserve it
        number.status = PhoneNumberStatus.RESERVED
        number.account_id = account_id
        number.reserved_until = now + timedelta(minutes=RESERVATION_TTL_MINUTES)

    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise NumberConflictError(f"{e164} was just reserved by another account") from e

    db.refresh(number)
    log_event(
        db,
        actor_id=account_id,
        action="number.reserved",
        target_type="phone_number",
        target_id=number.id,
        metadata={"e164": e164},
    )
    return number


def purchase_number(db: Session, account_id: str, e164: str) -> PhoneNumber:
    now = datetime.now(timezone.utc)
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()

    if number is None or number.account_id != account_id or number.status != PhoneNumberStatus.RESERVED:
        raise NumberConflictError(f"{e164} must be reserved by your account before purchase")
    if number.reserved_until is not None and number.reserved_until < now:
        raise NumberConflictError(f"Reservation for {e164} expired — reserve it again before purchasing")

    number.status = PhoneNumberStatus.PURCHASE_PENDING
    db.commit()
    log_event(
        db, actor_id=account_id, action="number.purchase_pending",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )

    try:
        bought = telecom.buy_number(e164)
    except telecom.TelecomError:
        # payment/provisioning failure must not strand the number silently —
        # release it back to Reserved so the customer can retry or it can expire
        number.status = PhoneNumberStatus.RESERVED
        db.commit()
        log_event(
            db, actor_id=account_id, action="number.purchase_failed",
            target_type="phone_number", target_id=number.id, metadata={"e164": e164},
        )
        raise

    number.status = PhoneNumberStatus.ACTIVE
    number.provider_sid = bought["sid"]
    number.reserved_until = None
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=account_id, action="number.activated",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "provider_sid": bought["sid"]},
    )

    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_number_activated(owner.email, e164)

    return number


def suspend_number(db: Session, account_id: str, e164: str, reason: str | None = None) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    if number is None or number.account_id != account_id or number.status != PhoneNumberStatus.ACTIVE:
        raise NumberConflictError(f"{e164} must be an active number owned by your account to suspend")

    number.status = PhoneNumberStatus.SUSPENDED
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=account_id, action="number.suspended",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "reason": reason},
    )
    return number


def cancel_number(db: Session, account_id: str, e164: str) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    if number is None or number.account_id != account_id or number.status not in (
        PhoneNumberStatus.ACTIVE, PhoneNumberStatus.SUSPENDED,
    ):
        raise NumberConflictError(f"{e164} must be an active or suspended number owned by your account to cancel")

    number.status = PhoneNumberStatus.CANCELLED
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=account_id, action="number.cancelled",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )
    return number


def configure_routing(
    db: Session,
    account_id: str,
    e164: str,
    forwarding_number: str | None,
    business_hours_start: time | None,
    business_hours_end: time | None,
    business_hours_timezone: str,
    ai_receptionist_enabled: bool = False,
) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")

    try:
        ZoneInfo(business_hours_timezone)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Unknown timezone: {business_hours_timezone}") from e

    number.forwarding_number = forwarding_number
    number.business_hours_start = business_hours_start
    number.business_hours_end = business_hours_end
    number.business_hours_timezone = business_hours_timezone
    number.ai_receptionist_enabled = ai_receptionist_enabled
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=account_id, action="number.routing_configured",
        target_type="phone_number", target_id=number.id,
        metadata={"e164": e164, "forwarding_number": forwarding_number},
    )
    return number


def list_account_numbers(db: Session, account_id: str) -> list[PhoneNumber]:
    return db.query(PhoneNumber).filter(PhoneNumber.account_id == account_id).all()
