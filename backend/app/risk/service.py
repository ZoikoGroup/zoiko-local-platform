from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.media.models import CallDirection, CallRecord
from app.numbering.numbers.service import suspend_numbers_for_account_by_system
from app.risk.models import BlockedDestination, RiskSignal, RiskSignalType

# Roadmap doc has no fixed number here - this is a conservative first pass
# threshold, not a tuned production value. Revisit once real traffic patterns
# are known.
VELOCITY_WINDOW_MINUTES = 5
MAX_OUTBOUND_CALLS_PER_WINDOW = 20

# Account risk scoring (Roadmap doc §13 Risk Register: "account risk
# scoring", "rapid suspension workflow"). Weights are a conservative first
# pass, same caveat as the velocity threshold above - a blocked-destination
# attempt is weighted higher than a velocity hit because it's evidence of
# deliberate abuse (dialing a known-bad prefix) rather than just a burst of
# otherwise-legitimate traffic.
RISK_SIGNAL_WINDOW_HOURS = 24
RISK_SIGNAL_WEIGHTS = {
    RiskSignalType.VELOCITY_EXCEEDED: 30,
    RiskSignalType.BLOCKED_DESTINATION_ATTEMPT: 40,
}
MAX_RISK_SCORE = 100
AUTO_SUSPEND_THRESHOLD = 100

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


def record_risk_signal(db: Session, *, account_id: str, signal_type: RiskSignalType, detail: str) -> RiskSignal:
    """Evidences a fraud/abuse rule actually firing against an account -
    called right before assert_destination_allowed/assert_outbound_velocity_ok
    raise, so the block itself leaves a queryable trail (doc's "no silent
    failure" principle) rather than only a rejected request the caller sees
    once. Also drives compute_account_risk_score below."""
    signal = RiskSignal(account_id=account_id, signal_type=signal_type, detail=detail)
    db.add(signal)
    db.commit()
    db.refresh(signal)
    log_event(
        db, actor="system:risk_engine", action=f"risk.{signal_type.value}",
        target=f"account:{account_id}", after={"detail": detail},
    )
    maybe_auto_suspend_for_risk(db, account_id)
    return signal


def assert_destination_allowed(db: Session, to_number: str, account_id: str) -> None:
    rule = is_destination_blocked(db, to_number)
    if rule is not None:
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.BLOCKED_DESTINATION_ATTEMPT,
            detail=f"dialed {to_number}, matched blocked prefix {rule.prefix} ({rule.reason})",
        )
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
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.VELOCITY_EXCEEDED,
            detail=f"{recent_count} outbound calls in {VELOCITY_WINDOW_MINUTES} minutes",
        )
        raise VelocityLimitExceededError(
            f"Outbound call rate limit exceeded: {recent_count} calls in the last "
            f"{VELOCITY_WINDOW_MINUTES} minutes (limit {MAX_OUTBOUND_CALLS_PER_WINDOW})"
        )


def compute_account_risk_score(db: Session, account_id: str) -> int:
    """Weighted count of RiskSignal rows in the trailing window, capped at
    MAX_RISK_SCORE - a repeatedly-abusive account scores higher than one that
    tripped a rule once, which a raw block/no-block signal can't distinguish."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=RISK_SIGNAL_WINDOW_HOURS)
    signals = (
        db.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.created_at >= window_start)
        .all()
    )
    score = sum(RISK_SIGNAL_WEIGHTS.get(s.signal_type, 0) for s in signals)
    return min(score, MAX_RISK_SCORE)


def get_account_risk_summary(db: Session, account_id: str) -> dict:
    window_start = datetime.now(timezone.utc) - timedelta(hours=RISK_SIGNAL_WINDOW_HOURS)
    signals = (
        db.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.created_at >= window_start)
        .order_by(RiskSignal.created_at.desc())
        .all()
    )
    score = min(sum(RISK_SIGNAL_WEIGHTS.get(s.signal_type, 0) for s in signals), MAX_RISK_SCORE)
    return {
        "account_id": account_id,
        "score": score,
        "auto_suspend_threshold": AUTO_SUSPEND_THRESHOLD,
        "window_hours": RISK_SIGNAL_WINDOW_HOURS,
        "signals": signals,
    }


def maybe_auto_suspend_for_risk(db: Session, account_id: str) -> bool:
    """Roadmap doc §13 Risk Register: "rapid suspension workflow" - crossing
    the threshold suspends every active number on the account immediately,
    without waiting for a human reviewer. Reversible: staff can reactivate
    via the normal number-activation path once reviewed, same as any other
    suspension reason."""
    score = compute_account_risk_score(db, account_id)
    if score < AUTO_SUSPEND_THRESHOLD:
        return False

    suspended = suspend_numbers_for_account_by_system(
        db, account_id, reason=f"risk: automatic suspension - risk score {score}/{MAX_RISK_SCORE}",
    )
    if suspended:
        log_event(
            db, actor="system:risk_engine", action="risk.account_auto_suspended",
            target=f"account:{account_id}",
            after={"score": score, "numbers_suspended": [n.e164 for n in suspended]},
        )
    return bool(suspended)


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
