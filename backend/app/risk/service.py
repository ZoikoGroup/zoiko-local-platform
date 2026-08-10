from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.media.models import CallDirection, CallRecord
from app.numbering.numbers.service import suspend_numbers_for_account_by_system
from app.risk.models import (
    BlockedDestination,
    DeviceFingerprintSighting,
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
# scoring", "rapid suspension workflow"). _DEFAULT_WEIGHTS is the fallback
# used when a signal type has no active FraudRule row (see
# get_signal_weight) - conservative first-pass values, same caveat as the
# velocity threshold above. A blocked-destination attempt outweighs a
# velocity hit because it's evidence of deliberate abuse (dialing a
# known-bad prefix) rather than just a burst of otherwise-legitimate
# traffic; geographic dispersion and spend-limit signals sit between the
# two - real IRSF/toll-abuse indicators, but each individually noisier
# than a known-bad-prefix hit.
RISK_SIGNAL_WINDOW_HOURS = 24
_DEFAULT_WEIGHTS = {
    RiskSignalType.VELOCITY_EXCEEDED: 30,
    RiskSignalType.BLOCKED_DESTINATION_ATTEMPT: 40,
    RiskSignalType.GEOGRAPHIC_DISPERSION: 35,
    RiskSignalType.SPEND_LIMIT_EXCEEDED: 35,
}
MAX_RISK_SCORE = 100
AUTO_SUSPEND_THRESHOLD = 100
# Roadmap doc §13 Risk Register "anomalous usage" - a lower tier than
# AUTO_SUSPEND_THRESHOLD that opens a FraudCase for human review instead of
# immediately suspending. Gives ops a rising-risk account to look at before
# it's severe enough to auto-suspend, not only after.
REVIEW_THRESHOLD = 70

# Commercial Billing Operating Standard doc's "real-time fraud/toll-abuse
# spend controls" - independent of call COUNT (velocity) or destination
# (geographic dispersion), a compromised account can rack up cost fast via
# a sustained string of calls to one expensive destination. Same
# "conservative first pass, not a tuned production value" caveat as every
# other threshold in this module - there's no real payment gateway yet to
# calibrate against actual customer spend patterns.
SPEND_WINDOW_HOURS = 24
MAX_SPEND_CENTS_PER_WINDOW = 5000  # $50.00

# IRSF-style dispersion check (Commercial Billing Operating Standard doc's
# fraud/toll-abuse ask + the pre-existing GEOGRAPHIC_DISPERSION signal type
# this module never actually implemented until now). Deliberately NOT a
# real E.164-to-country parse - this codebase has explicitly declined to
# build one elsewhere (see app.usage.models.CallingRate's docstring on the
# same gap for billing) - so this groups by to_number's leading digits as a
# coarse destination-cluster proxy instead of a resolved country. Good
# enough to catch "suddenly dialing many different regions," not precise
# enough to bill by.
GEOGRAPHIC_DISPERSION_WINDOW_MINUTES = 60
GEOGRAPHIC_DISPERSION_PREFIX_LEN = 3
MAX_DESTINATION_PREFIXES_PER_WINDOW = 5

# Architecture doc §5 "Fraud and Risk: device fingerprinting" - detection
# only, see RiskSignalType.DEVICE_FINGERPRINT_ABUSE's docstring for why
# this never blocks signup/login itself. Same "conservative first pass"
# caveat as every other threshold here.
DEVICE_FINGERPRINT_WINDOW_HOURS = 24
DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD = 4

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


class GeographicDispersionExceededError(Exception):
    """Raised when an account dials too many distinct destination-prefix
    clusters in a short window - an IRSF-style abuse pattern."""


class SpendLimitExceededError(Exception):
    """Raised when an account's rated outbound-call spend in the trailing
    window exceeds the configured threshold - Commercial Billing Operating
    Standard doc's "real-time fraud/toll-abuse spend controls" ask."""


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
    if not maybe_auto_suspend_for_risk(db, account_id):
        maybe_open_fraud_case_for_risk(db, account_id)
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


def get_signal_weight(db: Session, signal_type: RiskSignalType) -> int:
    """FraudRule as data (Architecture doc's "rules as data" doctrine,
    already established for ComplianceRule) instead of a hardcoded Python
    dict - staff can retune or disable a noisy signal without a deploy
    (see app.risk.models.FraudRule's docstring). A signal type with no
    active row falls back to the conservative built-in default."""
    rule = (
        db.query(FraudRule)
        .filter(FraudRule.signal_type == signal_type, FraudRule.is_active.is_(True))
        .first()
    )
    if rule is not None:
        return rule.weight
    return _DEFAULT_WEIGHTS.get(signal_type, 0)


def _weighted_score(db: Session, signals: list[RiskSignal]) -> int:
    return min(sum(get_signal_weight(db, s.signal_type) for s in signals), MAX_RISK_SCORE)


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
    return _weighted_score(db, signals)


def get_account_risk_summary(db: Session, account_id: str) -> dict:
    window_start = datetime.now(timezone.utc) - timedelta(hours=RISK_SIGNAL_WINDOW_HOURS)
    signals = (
        db.query(RiskSignal)
        .filter(RiskSignal.account_id == account_id, RiskSignal.created_at >= window_start)
        .order_by(RiskSignal.created_at.desc())
        .all()
    )
    score = _weighted_score(db, signals)
    return {
        "account_id": account_id,
        "score": score,
        "auto_suspend_threshold": AUTO_SUSPEND_THRESHOLD,
        "window_hours": RISK_SIGNAL_WINDOW_HOURS,
        "signals": signals,
    }


def assert_geographic_dispersion_ok(db: Session, to_number: str, account_id: str) -> None:
    """IRSF-style abuse pattern: a compromised account suddenly dials many
    different regions in a short window, unlike a legitimate cross-border
    business's steadier spread. Uses to_number's leading digits as a
    coarse destination-cluster proxy, not a resolved country - see this
    module's docstring constants for why a real E.164 parser is
    deliberately out of scope."""
    window_start = datetime.now(timezone.utc) - timedelta(minutes=GEOGRAPHIC_DISPERSION_WINDOW_MINUTES)
    recent_destinations = {
        row[0][:GEOGRAPHIC_DISPERSION_PREFIX_LEN]
        for row in db.query(CallRecord.to_number)
        .filter(
            CallRecord.account_id == account_id,
            CallRecord.direction == CallDirection.OUTBOUND,
            CallRecord.created_at >= window_start,
        )
        .all()
    }
    recent_destinations.add(to_number[:GEOGRAPHIC_DISPERSION_PREFIX_LEN])
    if len(recent_destinations) > MAX_DESTINATION_PREFIXES_PER_WINDOW:
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.GEOGRAPHIC_DISPERSION,
            detail=(
                f"{len(recent_destinations)} distinct destination prefixes in "
                f"{GEOGRAPHIC_DISPERSION_WINDOW_MINUTES} minutes"
            ),
        )
        raise GeographicDispersionExceededError(
            f"Too many distinct call destinations in a short window: {len(recent_destinations)} "
            f"in the last {GEOGRAPHIC_DISPERSION_WINDOW_MINUTES} minutes "
            f"(limit {MAX_DESTINATION_PREFIXES_PER_WINDOW})"
        )


def assert_spend_limit_ok(db: Session, account_id: str) -> None:
    """Commercial Billing Operating Standard doc's "real-time fraud/toll-
    abuse spend controls" - sums UsageEvent.estimated_cost_cents (the same
    rated figures app.usage.service.record_usage_event already writes)
    over the trailing window, independent of call count or destination."""
    from sqlalchemy import func as sa_func

    from app.usage.models import UsageEvent

    window_start = datetime.now(timezone.utc) - timedelta(hours=SPEND_WINDOW_HOURS)
    total_cents = (
        db.query(sa_func.coalesce(sa_func.sum(UsageEvent.estimated_cost_cents), 0))
        .filter(
            UsageEvent.account_id == account_id,
            UsageEvent.created_at >= window_start,
            UsageEvent.estimated_cost_cents.isnot(None),
        )
        .scalar()
    )
    if total_cents > MAX_SPEND_CENTS_PER_WINDOW:
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.SPEND_LIMIT_EXCEEDED,
            detail=f"{total_cents}c rated usage in the last {SPEND_WINDOW_HOURS}h (limit {MAX_SPEND_CENTS_PER_WINDOW}c)",
        )
        raise SpendLimitExceededError(
            f"Outbound call spend limit exceeded: {total_cents}c in the last {SPEND_WINDOW_HOURS}h "
            f"(limit {MAX_SPEND_CENTS_PER_WINDOW}c)"
        )


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


def maybe_open_fraud_case_for_risk(db: Session, account_id: str) -> FraudCase | None:
    """Roadmap doc §13 Risk Register "anomalous usage" - a rising-risk
    account gets a human-reviewable FraudCase once it crosses
    REVIEW_THRESHOLD, before (or instead of, if it never reaches
    AUTO_SUSPEND_THRESHOLD) an automatic suspension. Never opens a second
    OPEN case for the same account - one growing case, not a new row per
    signal."""
    score = compute_account_risk_score(db, account_id)
    if score < REVIEW_THRESHOLD:
        return None

    existing_open = (
        db.query(FraudCase)
        .filter(FraudCase.account_id == account_id, FraudCase.status == FraudCaseStatus.OPEN)
        .first()
    )
    if existing_open is not None:
        return None

    case = FraudCase(account_id=account_id, score_at_open=score, status=FraudCaseStatus.OPEN)
    db.add(case)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor="system:risk_engine", action="risk.fraud_case_opened",
        target=f"account:{account_id}", after={"score_at_open": score},
    )
    return case


def list_fraud_cases(db: Session, *, status: FraudCaseStatus | None = None) -> list[FraudCase]:
    query = db.query(FraudCase)
    if status is not None:
        query = query.filter(FraudCase.status == status)
    return query.order_by(FraudCase.created_at.desc()).all()


class FraudCaseNotFoundError(Exception):
    """Raised when resolving a fraud case id that doesn't exist."""


def resolve_fraud_case(
    db: Session, case_id: str, *, status: FraudCaseStatus, actor: str, notes: str
) -> FraudCase:
    """CONFIRMED or CLEARED, staff's call after reviewing the account's
    signal history - same "manual override reason" posture as every other
    sensitive staff action in this codebase."""
    case = db.query(FraudCase).filter(FraudCase.id == case_id).first()
    if case is None:
        raise FraudCaseNotFoundError(f"No fraud case {case_id!r}")

    case.status = status
    case.resolved_by = actor
    case.resolution_notes = notes
    case.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)

    log_event(
        db, actor=actor, action="risk.fraud_case_resolved", target=f"fraud_case:{case.id}",
        after={"status": status.value, "notes": notes},
    )
    return case


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


def record_fingerprint_sighting(db: Session, *, fingerprint_hash: str, account_id: str) -> None:
    """Called at signup (and optionally login) when the client sent a
    fingerprint hash - see DeviceFingerprintSighting's docstring. Silently
    a no-op design choice at the call site if the header is absent (never
    required), so this only ever gets called with a real value."""
    db.add(DeviceFingerprintSighting(fingerprint_hash=fingerprint_hash, account_id=account_id))
    db.commit()


def is_suspected_fingerprint_abuse(
    db: Session, fingerprint_hash: str, candidate_account_id: str | None = None
) -> bool:
    """True when fingerprint_hash has touched DEVICE_FINGERPRINT_ACCOUNT_
    THRESHOLD+ distinct accounts (platform-wide) within
    DEVICE_FINGERPRINT_WINDOW_HOURS - the free-trial/quota abuse pattern of
    one device spinning up many accounts. Same shape as
    is_suspected_spam_caller; candidate_account_id folds in the account
    that just signed up, before its own sighting row exists yet, for the
    same reason documented there."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=DEVICE_FINGERPRINT_WINDOW_HOURS)
    accounts = {
        row[0]
        for row in db.query(DeviceFingerprintSighting.account_id)
        .filter(
            DeviceFingerprintSighting.fingerprint_hash == fingerprint_hash,
            DeviceFingerprintSighting.created_at >= window_start,
        )
        .distinct()
        .all()
    }
    if candidate_account_id is not None:
        accounts.add(candidate_account_id)
    return len(accounts) >= DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD


def check_fingerprint_on_signup(db: Session, *, fingerprint_hash: str | None, account_id: str) -> None:
    """Wired into signup (see app.numbering.identity.service.
    create_account_with_owner) - records the sighting and raises a
    RiskSignal if the fingerprint has now touched too many accounts.
    Never raises/blocks: detection only, for the review queue
    (maybe_open_fraud_case_for_risk), not a hard signup gate - a coarse
    client-side fingerprint has real false-positive risk (shared office
    network, family device)."""
    if not fingerprint_hash:
        return
    if is_suspected_fingerprint_abuse(db, fingerprint_hash, candidate_account_id=account_id):
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.DEVICE_FINGERPRINT_ABUSE,
            detail=f"fingerprint seen across {DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD}+ accounts in "
                   f"{DEVICE_FINGERPRINT_WINDOW_HOURS}h",
        )
    record_fingerprint_sighting(db, fingerprint_hash=fingerprint_hash, account_id=account_id)


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
