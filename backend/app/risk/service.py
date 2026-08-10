import math
from datetime import datetime, timedelta, timezone

import phonenumbers
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.media.models import CallDirection, CallRecord
from app.numbering.numbers.service import suspend_numbers_for_account_by_system
from app.risk.models import (
    BlockedDestination,
    FraudCase,
    FraudCaseStatus,
    FraudRule,
    RiskSignal,
    RiskSignalType,
)

# Roadmap doc has no fixed number here - this is a conservative first pass
# threshold, not a tuned production value. Revisit once real traffic patterns
# are known.
VELOCITY_WINDOW_MINUTES = 5
MAX_OUTBOUND_CALLS_PER_WINDOW = 20

# Account risk scoring (Roadmap doc §13 Risk Register: "account risk
# scoring", "rapid suspension workflow"; Architecture doc Phase 4 "proprietary
# fraud models"). Defaults are a conservative first pass, same caveat as the
# velocity threshold above - a blocked-destination attempt is weighted higher
# than a velocity hit because it's evidence of deliberate abuse (dialing a
# known-bad prefix) rather than just a burst of otherwise-legitimate traffic.
# These are only the FALLBACK - see get_signal_weight, which prefers a
# staff-tunable FraudRule row over this dict, same "rules as data" doctrine
# ComplianceRule already follows.
RISK_SIGNAL_WINDOW_HOURS = 24
_DEFAULT_WEIGHTS = {
    RiskSignalType.VELOCITY_EXCEEDED: 30,
    RiskSignalType.BLOCKED_DESTINATION_ATTEMPT: 40,
    RiskSignalType.GEOGRAPHIC_DISPERSION: 25,
}
MAX_RISK_SCORE = 100
AUTO_SUSPEND_THRESHOLD = 100
# A rising-risk account that hasn't yet earned instant suspension still
# deserves a human look - the gap the old binary "100+ -> suspend,
# otherwise nothing visible" design left. See open_fraud_case_if_needed.
REVIEW_THRESHOLD = 60
# Exponential decay: a signal from `RISK_SIGNAL_WINDOW_HOURS` ago still
# counts (it's inside the window) but contributes far less than one from
# the last hour - a repeatedly-abusive account concentrated in a short
# burst now scores meaningfully higher than one with the same signal count
# spread thinly across the whole day, which a flat sum can't distinguish.
RISK_DECAY_HALF_LIFE_HOURS = 8.0

# International Revenue Share Fraud (IRSF) pattern: outbound calls to many
# distinct countries in a short window, a classic sign of a compromised
# account or stolen credentials being used to dial premium international
# ranges - a real cross-border business's own calling pattern is normally
# far steadier. Same "not a tuned production value" caveat as the other
# thresholds in this module.
GEOGRAPHIC_DISPERSION_WINDOW_MINUTES = 10
GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD = 5

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


class GeographicDispersionError(Exception):
    """Raised when an account's outbound calls fan out across too many
    distinct countries too quickly (IRSF pattern)."""


class DestinationRuleConflictError(Exception):
    """Raised when adding a blocked-destination prefix that already exists."""


def is_destination_blocked(db: Session, to_number: str) -> BlockedDestination | None:
    for rule in db.query(BlockedDestination).all():
        if to_number.startswith(rule.prefix):
            return rule
    return None


def get_signal_weight(db: Session, signal_type: RiskSignalType) -> int:
    """Staff-tunable weight for one signal type - a FraudRule row overrides
    the built-in default (0 if staff have explicitly deactivated the
    signal); no row at all falls back to _DEFAULT_WEIGHTS. Lets the fraud
    model be retuned, or a noisy signal silenced, without a code deploy."""
    rule = db.query(FraudRule).filter(FraudRule.signal_type == signal_type).first()
    if rule is not None:
        return rule.weight if rule.is_active else 0
    return _DEFAULT_WEIGHTS.get(signal_type, 0)


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
    if maybe_auto_suspend_for_risk(db, account_id):
        _auto_resolve_open_case_for_suspended_account(db, account_id)
    else:
        open_fraud_case_if_needed(db, account_id)
    return signal


def _auto_resolve_open_case_for_suspended_account(db: Session, account_id: str) -> None:
    """A case can open at REVIEW_THRESHOLD on one signal and the account can
    cross AUTO_SUSPEND_THRESHOLD on a later one - without this, that earlier
    case is left dangling in OPEN status even though the account has since
    been suspended, which reads to a reviewer as "still needs a look" for an
    account the system already acted on. Auto-suspension is stronger
    evidence than the review-tier trigger, so it supersedes the case rather
    than leaving it stale."""
    case = (
        db.query(FraudCase)
        .filter(FraudCase.account_id == account_id, FraudCase.status == FraudCaseStatus.OPEN)
        .first()
    )
    if case is not None:
        resolve_fraud_case(
            db, case.id, status=FraudCaseStatus.CONFIRMED, resolved_by="system:risk_engine",
            notes="Auto-resolved: account was auto-suspended before human review completed.",
        )


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


def _country_for_e164(e164: str) -> str | None:
    """Best-effort E.164 -> ISO country code, matching the 2-letter format
    already used by PhoneNumber.country. Returns None for a number
    phonenumbers can't parse (e.g. malformed input) rather than raising -
    a parse failure isn't itself fraud evidence."""
    try:
        parsed = phonenumbers.parse(e164 if e164.startswith("+") else f"+{e164}")
    except phonenumbers.NumberParseException:
        return None
    return phonenumbers.region_code_for_number(parsed)


def assert_geographic_dispersion_ok(db: Session, account_id: str, to_number: str) -> None:
    """IRSF-pattern guard: blocks the call (and records a signal) once an
    account's outbound calls in the trailing window have touched
    GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD+ distinct countries, counting
    the destination about to be dialed. Same "count existing, decide before
    this one lands" shape as assert_outbound_velocity_ok."""
    destination_country = _country_for_e164(to_number)
    if destination_country is None:
        return  # can't classify this destination's country - nothing to disperse-check

    window_start = datetime.now(timezone.utc) - timedelta(minutes=GEOGRAPHIC_DISPERSION_WINDOW_MINUTES)
    recent_destinations = (
        db.query(CallRecord.to_number)
        .filter(
            CallRecord.account_id == account_id,
            CallRecord.direction == CallDirection.OUTBOUND,
            CallRecord.created_at >= window_start,
        )
        .all()
    )
    countries = {_country_for_e164(row[0]) for row in recent_destinations}
    countries.discard(None)
    countries.add(destination_country)

    if len(countries) >= GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD:
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.GEOGRAPHIC_DISPERSION,
            detail=(
                f"outbound calls to {len(countries)} distinct countries "
                f"({', '.join(sorted(countries))}) in {GEOGRAPHIC_DISPERSION_WINDOW_MINUTES} minutes"
            ),
        )
        raise GeographicDispersionError(
            f"Outbound calling pattern blocked: {len(countries)} distinct countries dialed in "
            f"{GEOGRAPHIC_DISPERSION_WINDOW_MINUTES} minutes (limit {GEOGRAPHIC_DISPERSION_COUNTRY_THRESHOLD})"
        )


def _decayed_score(db: Session, signals: list[RiskSignal]) -> int:
    """Each signal's weight decays exponentially by age within the window -
    a burst of abuse in the last hour scores far higher than the same
    signal count spread thinly across the full RISK_SIGNAL_WINDOW_HOURS,
    which a flat sum can't distinguish. Half-life, not a hard cutoff, so
    the score glides down smoothly rather than falling off a cliff exactly
    at the window boundary."""
    now = datetime.now(timezone.utc)
    total = 0.0
    for signal in signals:
        age_hours = (now - signal.created_at).total_seconds() / 3600
        weight = get_signal_weight(db, signal.signal_type)
        total += weight * (0.5 ** (age_hours / RISK_DECAY_HALF_LIFE_HOURS))
    return min(round(total), MAX_RISK_SCORE)


def compute_account_risk_score(db: Session, account_id: str) -> int:
    """Time-decayed weighted score of RiskSignal rows in the trailing
    window, capped at MAX_RISK_SCORE - see _decayed_score."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=RISK_SIGNAL_WINDOW_HOURS)
    signals = (
        db.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.created_at >= window_start)
        .all()
    )
    return _decayed_score(db, signals)


def get_account_risk_summary(db: Session, account_id: str) -> dict:
    window_start = datetime.now(timezone.utc) - timedelta(hours=RISK_SIGNAL_WINDOW_HOURS)
    signals = (
        db.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.created_at >= window_start)
        .order_by(RiskSignal.created_at.desc())
        .all()
    )
    return {
        "account_id": account_id,
        "score": _decayed_score(db, signals),
        "auto_suspend_threshold": AUTO_SUSPEND_THRESHOLD,
        "review_threshold": REVIEW_THRESHOLD,
        "window_hours": RISK_SIGNAL_WINDOW_HOURS,
        "signals": signals,
    }


def maybe_auto_suspend_for_risk(db: Session, account_id: str) -> bool:
    """Roadmap doc §13 Risk Register: "rapid suspension workflow" - crossing
    the threshold suspends every active number on the account immediately,
    without waiting for a human reviewer. Reversible: staff can reactivate
    via the normal number-activation path once reviewed, same as any other
    suspension reason. Returns True whenever the threshold was crossed
    (regardless of whether the account had any active number left to
    suspend) - callers use this to decide whether the lower-tier review
    queue (open_fraud_case_if_needed) still applies."""
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
    return True


def open_fraud_case_if_needed(db: Session, account_id: str) -> FraudCase | None:
    """Human-in-the-loop review queue (Architecture doc Phase 4 "proprietary
    fraud models") - opens a case once the decayed score crosses
    REVIEW_THRESHOLD but the account hasn't (yet) hit AUTO_SUSPEND_THRESHOLD.
    At most one OPEN case per account at a time - repeated signals while a
    case is already open just accumulate more evidence for the reviewer,
    not a pile of duplicate cases."""
    score = compute_account_risk_score(db, account_id)
    if score < REVIEW_THRESHOLD:
        return None

    existing_open = (
        db.query(FraudCase)
        .filter(FraudCase.account_id == account_id, FraudCase.status == FraudCaseStatus.OPEN)
        .first()
    )
    if existing_open is not None:
        return existing_open

    case = FraudCase(account_id=account_id, score_at_open=score)
    db.add(case)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor="system:risk_engine", action="risk.fraud_case_opened",
        target=f"account:{account_id}", after={"case_id": case.id, "score": score},
    )
    return case


def list_fraud_cases(db: Session, status: FraudCaseStatus | None = None) -> list[FraudCase]:
    query = db.query(FraudCase)
    if status is not None:
        query = query.filter(FraudCase.status == status)
    return query.order_by(FraudCase.created_at.desc()).all()


class FraudCaseNotFoundError(Exception):
    """Raised when a fraud_case id doesn't exist."""


class FraudCaseAlreadyResolvedError(Exception):
    """Raised when trying to resolve a case that's already CONFIRMED or CLEARED."""


class InvalidFraudCaseResolutionError(Exception):
    """Raised when a resolution status other than CONFIRMED/CLEARED is given -
    OPEN isn't a resolution, only the case's own starting state."""


def resolve_fraud_case(
    db: Session, case_id: str, *, status: FraudCaseStatus, resolved_by: str, notes: str | None = None
) -> FraudCase:
    if status == FraudCaseStatus.OPEN:
        raise InvalidFraudCaseResolutionError("A case can only be resolved as 'confirmed' or 'cleared'")
    case = db.query(FraudCase).filter(FraudCase.id == case_id).first()
    if case is None:
        raise FraudCaseNotFoundError(f"No such fraud case {case_id!r}")
    if case.status != FraudCaseStatus.OPEN:
        raise FraudCaseAlreadyResolvedError(f"Case {case_id} is already {case.status.value}")

    case.status = status
    case.resolved_by = resolved_by
    case.resolution_notes = notes
    case.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=resolved_by, action="risk.fraud_case_resolved",
        target=f"fraud_case:{case.id}", after={"status": status.value, "notes": notes},
    )
    return case


def list_fraud_rules(db: Session) -> list[FraudRule]:
    return db.query(FraudRule).order_by(FraudRule.signal_type).all()


def upsert_fraud_rule(
    db: Session, *, signal_type: RiskSignalType, weight: int, is_active: bool, actor: str
) -> FraudRule:
    rule = db.query(FraudRule).filter(FraudRule.signal_type == signal_type).first()
    if rule is None:
        rule = FraudRule(signal_type=signal_type, weight=weight, is_active=is_active)
        db.add(rule)
    else:
        rule.weight = weight
        rule.is_active = is_active
    db.commit()
    db.refresh(rule)
    log_event(
        db, actor=actor, action="risk.fraud_rule_updated",
        target=f"fraud_rule:{rule.id}", after={"signal_type": signal_type.value, "weight": weight, "is_active": is_active},
    )
    return rule


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
