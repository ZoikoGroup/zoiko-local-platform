from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.media.models import CallDirection, CallRecord
from app.risk.models import BlockedDestination

# Roadmap doc has no fixed number here - this is a conservative first pass
# threshold, not a tuned production value. Revisit once real traffic patterns
# are known.
VELOCITY_WINDOW_MINUTES = 5
MAX_OUTBOUND_CALLS_PER_WINDOW = 20

# Inbound fraud/spam signal (Roadmap "AI-driven fraud/spam signals"): a real
# customer calls one business; a robocall/spam campaign dials the same
# number out to many businesses in a short window. Platform-wide (not
# per-account) by design - the whole point is spotting a pattern no single
# account's own call history would ever show. Same "not a tuned production
# value" caveat as the outbound velocity threshold above.
INBOUND_SPAM_WINDOW_MINUTES = 60
INBOUND_SPAM_ACCOUNT_THRESHOLD = 3


class DestinationBlockedError(Exception):
    """Raised when an outbound call targets a blocked destination prefix."""


class VelocityLimitExceededError(Exception):
    """Raised when an account places outbound calls faster than the fraud
    velocity threshold allows."""


class DestinationRuleConflictError(Exception):
    """Raised when adding a blocked-destination prefix that already exists."""


def is_destination_blocked(db: Session, to_number: str) -> BlockedDestination | None:
    for rule in db.query(BlockedDestination).all():
        if to_number.startswith(rule.prefix):
            return rule
    return None


def assert_destination_allowed(db: Session, to_number: str) -> None:
    rule = is_destination_blocked(db, to_number)
    if rule is not None:
        raise DestinationBlockedError(f"{to_number} matches a blocked destination rule ({rule.reason})")


def assert_outbound_velocity_ok(db: Session, account_id: str) -> None:
    window_start = datetime.now(timezone.utc) - timedelta(minutes=VELOCITY_WINDOW_MINUTES)
    recent_count = (
        db.query(CallRecord)
        .filter(
            CallRecord.account_id == account_id,
            CallRecord.direction == CallDirection.OUTBOUND,
            CallRecord.created_at >= window_start,
        )
        .count()
    )
    if recent_count >= MAX_OUTBOUND_CALLS_PER_WINDOW:
        raise VelocityLimitExceededError(
            f"Outbound call rate limit exceeded: {recent_count} calls in the last "
            f"{VELOCITY_WINDOW_MINUTES} minutes (limit {MAX_OUTBOUND_CALLS_PER_WINDOW})"
        )


def is_suspected_spam_caller(db: Session, from_number: str, candidate_account_id: str | None = None) -> bool:
    """True when from_number has called INBOUND_SPAM_ACCOUNT_THRESHOLD+
    distinct accounts (platform-wide) within the last INBOUND_SPAM_WINDOW_MINUTES.
    Checked at record_call() time for every inbound call, regardless of which
    number it's calling - a single account's own history could never surface
    this, since each call it sees is just one data point.

    candidate_account_id: the CURRENT call's account, checked BEFORE that
    call's own CallRecord row exists yet - without folding it in, the call
    that actually crosses the threshold would itself go unflagged (only the
    next one would), since it isn't in the DB yet at decision time.
    """
    window_start = datetime.now(timezone.utc) - timedelta(minutes=INBOUND_SPAM_WINDOW_MINUTES)
    accounts = {
        row[0]
        for row in db.query(CallRecord.account_id)
        .filter(
            CallRecord.direction == CallDirection.INBOUND,
            CallRecord.from_number == from_number,
            CallRecord.created_at >= window_start,
            CallRecord.account_id.isnot(None),
        )
        .distinct()
        .all()
    }
    if candidate_account_id is not None:
        accounts.add(candidate_account_id)
    return len(accounts) >= INBOUND_SPAM_ACCOUNT_THRESHOLD


def add_blocked_destination(db: Session, *, prefix: str, reason: str, actor: str) -> BlockedDestination:
    rule = BlockedDestination(prefix=prefix, reason=reason)
    db.add(rule)
    try:
        db.commit()
    except IntegrityError as e:
        db.rollback()
        raise DestinationRuleConflictError(f"A blocked-destination rule for {prefix!r} already exists") from e
    db.refresh(rule)
    log_event(
        db, actor=actor, action="risk.destination_blocked",
        target=f"blocked_destination:{rule.id}", after={"prefix": prefix, "reason": reason},
    )
    return rule


def list_blocked_destinations(db: Session) -> list[BlockedDestination]:
    return db.query(BlockedDestination).order_by(BlockedDestination.created_at.desc()).all()


def remove_blocked_destination(db: Session, rule_id: str, actor: str) -> None:
    rule = db.query(BlockedDestination).filter(BlockedDestination.id == rule_id).first()
    if rule is None:
        return
    prefix = rule.prefix
    db.delete(rule)
    db.commit()
    log_event(
        db, actor=actor, action="risk.destination_unblocked",
        target=f"blocked_destination:{rule_id}", before={"prefix": prefix},
    )
