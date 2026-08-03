from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.compliance.service import has_approved_case, is_requirement_active
from app.core.config import settings
from app.integrations.telecom import twilio as telecom
from app.notifications.service import notify_number_activated, notify_number_suspended
from app.numbering.identity.models import Account, AccountType, User, UserRole
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

RESERVATION_TTL_MINUTES = 12


class NumberConflictError(Exception):
    """Raised when a number can't be reserved/purchased because another
    account already holds it, or the caller's own reservation lapsed."""


class ComplianceRequiredError(Exception):
    """Raised when the number's country has an active KYC/KYB rule and the
    account has no approved compliance case covering it yet — the docs'
    "Compliance Pending" lifecycle state, enforced at the point of purchase."""


def _kyc_requirement_type(db: Session, account_id: str) -> str:
    account = db.query(Account).filter(Account.id == account_id).first()
    return "kyc_individual" if account.account_type == AccountType.INDIVIDUAL else "kyc_business"


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

    requirement_type = _kyc_requirement_type(db, account_id)
    if is_requirement_active(db, number.country, requirement_type) and not has_approved_case(
        db, account_id=account_id, jurisdiction=number.country, requirement_type=requirement_type
    ):
        raise ComplianceRequiredError(
            f"An approved {requirement_type} compliance case for {number.country} "
            "is required before purchasing a number there"
        )

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


def suspend_number(db: Session, user: User, e164: str, reason: str | None = None) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    if number is None or number.account_id != user.account_id or number.status != PhoneNumberStatus.ACTIVE:
        raise NumberConflictError(f"{e164} must be an active number owned by your account to suspend")
    assert_number_access(number, user)

    number.status = PhoneNumberStatus.SUSPENDED
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=user.id, action="number.suspended",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164, "reason": reason},
    )

    owner = db.query(User).filter(User.account_id == user.account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_number_suspended(owner.email, e164, reason)

    return number


def cancel_number(db: Session, user: User, e164: str) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).with_for_update().first()
    if number is None or number.account_id != user.account_id or number.status not in (
        PhoneNumberStatus.ACTIVE, PhoneNumberStatus.SUSPENDED,
    ):
        raise NumberConflictError(f"{e164} must be an active or suspended number owned by your account to cancel")
    assert_number_access(number, user)

    # Release on Twilio *before* marking cancelled locally - if this fails,
    # the number stays ACTIVE/SUSPENDED here too, so the customer isn't left
    # thinking it's cancelled while it's still live (and billing) for real.
    if number.provider_sid:
        telecom.release_number(number.provider_sid)

    number.status = PhoneNumberStatus.CANCELLED
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=user.id, action="number.cancelled",
        target_type="phone_number", target_id=number.id, metadata={"e164": e164},
    )
    return number


def configure_routing(
    db: Session,
    user: User,
    e164: str,
    forwarding_number: str | None,
    business_hours_start: time | None,
    business_hours_end: time | None,
    business_hours_timezone: str,
    ai_receptionist_enabled: bool = False,
    escalation_user_id: str | None = None,
) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")
    assert_number_access(number, user)

    try:
        ZoneInfo(business_hours_timezone)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Unknown timezone: {business_hours_timezone}") from e

    if escalation_user_id is not None:
        nominee = db.query(User).filter(User.id == escalation_user_id, User.account_id == user.account_id).first()
        if nominee is None:
            raise NumberConflictError(f"No team member with id {escalation_user_id} on this account")

    number.forwarding_number = forwarding_number
    number.business_hours_start = business_hours_start
    number.business_hours_end = business_hours_end
    number.business_hours_timezone = business_hours_timezone
    number.ai_receptionist_enabled = ai_receptionist_enabled
    number.escalation_user_id = escalation_user_id
    db.commit()
    db.refresh(number)
    log_event(
        db, actor_id=user.id, action="number.routing_configured",
        target_type="phone_number", target_id=number.id,
        metadata={
            "e164": e164,
            "forwarding_number": forwarding_number,
            "escalation_user_id": escalation_user_id,
        },
    )
    return number


def sync_webhook(db: Session, user: User, e164: str) -> PhoneNumber:
    """(Re)points this number's Twilio voice webhook at the current
    PUBLIC_BASE_URL - needed whenever that URL changes (e.g. a fresh ngrok
    tunnel in dev, since free-tier ngrok issues a new URL every restart and
    buy_number() only wires this up once, at purchase time)."""
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != user.account_id or number.provider_sid is None:
        raise NumberConflictError(f"{e164} must be a purchased number owned by your account")
    assert_number_access(number, user)

    if not settings.public_base_url:
        raise NumberConflictError("PUBLIC_BASE_URL is not configured - nothing to point the webhook at")

    telecom.set_voice_webhook(number.provider_sid, settings.public_base_url)
    log_event(
        db, actor_id=user.id, action="number.webhook_synced",
        target_type="phone_number", target_id=number.id,
        metadata={"e164": e164, "public_base_url": settings.public_base_url},
    )
    return number


def list_account_numbers(db: Session, account_id: str, *, user: User) -> list[PhoneNumber]:
    """Owner/Admin see every number on the account. A plain Member only sees
    numbers assigned to them - unassigned numbers aren't theirs to manage
    yet, they haven't been handed anything."""
    query = db.query(PhoneNumber).filter(PhoneNumber.account_id == account_id)
    if user.role == UserRole.MEMBER:
        query = query.filter(PhoneNumber.assigned_user_id == user.id)
    return query.all()


def assigned_number_ids(db: Session, user: User) -> list[str] | None:
    """Number IDs a Member is scoped to across calls/voicemail/video/AI
    summaries - the same assignment boundary `assert_number_access` enforces
    for direct number management. Returns None for Owner/Admin, meaning
    "no restriction" rather than "empty list" (which would hide everything)."""
    if user.role != UserRole.MEMBER:
        return None
    rows = (
        db.query(PhoneNumber.id)
        .filter(PhoneNumber.account_id == user.account_id, PhoneNumber.assigned_user_id == user.id)
        .all()
    )
    return [r[0] for r in rows]


def assign_number(db: Session, *, account_id: str, e164: str, user_id: str | None, actor: str) -> PhoneNumber:
    number = db.query(PhoneNumber).filter(PhoneNumber.e164 == e164).first()
    if number is None or number.account_id != account_id:
        raise NumberConflictError(f"{e164} is not a number owned by your account")

    if user_id is not None:
        assignee = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
        if assignee is None:
            raise NumberConflictError(f"No team member with id {user_id} on this account")

    before_assignee = number.assigned_user_id
    number.assigned_user_id = user_id
    db.commit()
    db.refresh(number)

    log_event(
        db, actor=actor, action="number.assigned",
        target=f"phone_number:{number.id}",
        before={"assigned_user_id": before_assignee},
        after={"assigned_user_id": user_id},
    )
    return number


def assert_number_access(number: PhoneNumber, user: User) -> None:
    """Owner/Admin can manage any number on their account. A Member can only
    manage a number that's been assigned to them. Shared by every module
    (calls, voicemail, AI summaries, routing) that gates a per-number action
    on the same assignment boundary."""
    if user.role == UserRole.MEMBER and number.assigned_user_id != user.id:
        raise NumberConflictError(f"{number.e164} is not assigned to you")
