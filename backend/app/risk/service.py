import phonenumbers
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.media.models import TERMINAL_CALL_STATUSES, CallDirection, CallRecord
from app.notifications.service import notify_account_suspended_for_risk, notify_account_warning
from app.numbering.identity.models import Account
from app.numbering.numbers.service import suspend_numbers_for_account_by_system
from app.risk.models import (
    AccountRiskState,
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
    RiskSignalType.SPEND_LIMIT_EXCEEDED: 35,
    RiskSignalType.CONCURRENT_CALL_LIMIT_EXCEEDED: 20,
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

# Commercial Billing Operating Standard doc's "real-time fraud/toll-abuse
# spend controls" - independent of call COUNT (velocity) or destination
# (geographic dispersion), a compromised account can rack up cost fast via
# a sustained string of calls to one expensive destination. Same
# "conservative first pass, not a tuned production value" caveat as every
# other threshold in this module - there's no real payment gateway yet to
# calibrate against actual customer spend patterns.
SPEND_WINDOW_HOURS = 24
MAX_SPEND_CENTS_PER_WINDOW = 5000  # $50.00

# Architecture doc §5 "Fraud and Risk: device fingerprinting" - detection
# only, see RiskSignalType.DEVICE_FINGERPRINT_ABUSE's docstring for why
# this never blocks signup/login itself. Same "conservative first pass"
# caveat as every other threshold here.
DEVICE_FINGERPRINT_WINDOW_HOURS = 24
DEVICE_FINGERPRINT_ACCOUNT_THRESHOLD = 4

# Production Readiness Standard doc's "trial-abuse step-up model" - how many
# outbound calls an account may have in flight AT ONCE, keyed by its current
# AccountRiskState tier. Same "conservative first pass, not a tuned
# production value" caveat as every other threshold in this module.
# SUSPENDED_FRAUD is 0 (blocks all outbound calling outright); REVIEW_REQUIRED
# is deliberately as tight as TRIAL_LOW - an account under active fraud
# review gets no more trust than a brand-new signup until a human clears it.
MAX_CONCURRENT_CALLS_BY_RISK_STATE = {
    AccountRiskState.TRIAL_LOW: 1,
    AccountRiskState.TRIAL_VERIFIED: 3,
    AccountRiskState.PAID_NORMAL: 10,
    AccountRiskState.REVIEW_REQUIRED: 1,
    AccountRiskState.SUSPENDED_FRAUD: 0,
}

# Inbound fraud/spam signal (Roadmap "AI-driven fraud/spam signals"): a real
# customer calls one business; a robocall/spam campaign dials the same
# number out to many businesses in a short window. Platform-wide (not
# per-account) by design - the whole point is spotting a pattern no single
# account's own call history would ever show. Same "not a tuned production
# value" caveat as the outbound velocity threshold above.
INBOUND_SPAM_WINDOW_MINUTES = 60
INBOUND_SPAM_ACCOUNT_THRESHOLD = 3


def _get_account_owner(db: Session, account_id: str):
    from app.numbering.identity.models import User, UserRole

    return db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()


class DestinationBlockedError(Exception):
    """Raised when an outbound call targets a blocked destination prefix."""


class VelocityLimitExceededError(Exception):
    """Raised when an account places outbound calls faster than the fraud
    velocity threshold allows."""


class GeographicDispersionError(Exception):
    """Raised when an account's outbound calls fan out across too many
    distinct countries too quickly (IRSF pattern)."""


class SpendLimitExceededError(Exception):
    """Raised when an account's rated outbound-call spend in the trailing
    window exceeds the configured threshold - Commercial Billing Operating
    Standard doc's "real-time fraud/toll-abuse spend controls" ask."""


class ConcurrentCallLimitExceededError(Exception):
    """Raised when an account already has as many outbound calls in flight
    as its current AccountRiskState tier allows - see
    MAX_CONCURRENT_CALLS_BY_RISK_STATE."""


class DestinationRuleConflictError(Exception):
    """Raised when adding a blocked-destination prefix that already exists."""


class AccountNotFoundError(Exception):
    """Raised when set_account_risk_state is called for an account_id that
    doesn't exist."""


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
            db, case.id, status=FraudCaseStatus.CONFIRMED, actor="system:risk_engine",
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


def assert_concurrent_call_limit_ok(db: Session, account_id: str) -> None:
    """Production Readiness Standard doc's "trial-abuse step-up model" -
    unlike assert_outbound_velocity_ok (calls per rolling time window), this
    counts calls that are IN FLIGHT right now (status not yet terminal) and
    compares against the account's current AccountRiskState tier, not a
    single platform-wide number. A brand-new TRIAL_LOW account trying to
    run several outbound calls simultaneously is stopped here even if each
    individual call is well under the velocity window's rate limit."""
    account = db.query(Account).filter(Account.id == account_id).first()
    risk_state = account.risk_state if account is not None else AccountRiskState.TRIAL_LOW
    limit = MAX_CONCURRENT_CALLS_BY_RISK_STATE[risk_state]

    in_flight_count = (
        db.query(CallRecord)
        .filter(
            CallRecord.account_id == account_id,
            CallRecord.direction == CallDirection.OUTBOUND,
            CallRecord.status.notin_(TERMINAL_CALL_STATUSES),
        )
        .count()
    )
    if in_flight_count >= limit:
        record_risk_signal(
            db, account_id=account_id, signal_type=RiskSignalType.CONCURRENT_CALL_LIMIT_EXCEEDED,
            detail=f"{in_flight_count} outbound calls already in flight ({risk_state.value} tier, limit {limit})",
        )
        raise ConcurrentCallLimitExceededError(
            f"Concurrent outbound call limit exceeded: {in_flight_count} calls already in flight "
            f"(limit {limit} for the {risk_state.value} tier)"
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
    this one lands" shape as assert_outbound_velocity_ok. Uses a real
    E.164-to-country parse (phonenumbers), not a coarse proxy."""
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


def assert_spend_limit_ok(db: Session, account_id: str) -> None:
    """Commercial Billing Operating Standard doc's "real-time fraud/toll-
    abuse spend controls" - sums UsageEvent.estimated_cost_cents (the same
    rated figures app.usage.service.record_usage_event already writes)
    over the trailing window, independent of call count or destination.
    Anil's anilupdated branch didn't build this leg of the fraud model -
    kept from the merge's other side."""
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
    account = db.query(Account).filter(Account.id == account_id).first()
    return {
        "account_id": account_id,
        "score": _decayed_score(db, signals),
        "risk_state": account.risk_state if account is not None else AccountRiskState.TRIAL_LOW,
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
    _transition_risk_state(
        db, account_id, AccountRiskState.SUSPENDED_FRAUD, actor="system:risk_engine",
        reason=f"automatic suspension - risk score {score}/{MAX_RISK_SCORE}",
    )
    if suspended:
        log_event(
            db, actor="system:risk_engine", action="risk.account_auto_suspended",
            target=f"account:{account_id}",
            after={"score": score, "numbers_suspended": [n.e164 for n in suspended]},
        )
        owner = _get_account_owner(db, account_id)
        if owner is not None:
            notify_account_suspended_for_risk(
                db, account_id=account_id, account_email=owner.email,
                reason_category=f"risk score {score}/{MAX_RISK_SCORE}", case_reference=account_id,
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
    _transition_risk_state(
        db, account_id, AccountRiskState.REVIEW_REQUIRED, actor="system:risk_engine",
        reason=f"fraud case {case.id} opened - risk score {score}/{MAX_RISK_SCORE}",
    )
    owner = _get_account_owner(db, account_id)
    if owner is not None:
        signal_types = {
            s.signal_type.value
            for s in db.query(RiskSignal).filter(RiskSignal.account_id == account_id).order_by(
                RiskSignal.created_at.desc()
            ).limit(5)
        }
        notify_account_warning(
            db, account_id=account_id, account_email=owner.email,
            policy_area=", ".join(sorted(signal_types)) or "unusual account activity",
            case_reference=case.id,
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
    db: Session, case_id: str, *, status: FraudCaseStatus, actor: str, notes: str | None = None
) -> FraudCase:
    """CONFIRMED or CLEARED, staff's call after reviewing the account's
    signal history - same "manual override reason" posture as every other
    sensitive staff action in this codebase."""
    if status == FraudCaseStatus.OPEN:
        raise InvalidFraudCaseResolutionError("A case can only be resolved as 'confirmed' or 'cleared'")
    case = db.query(FraudCase).filter(FraudCase.id == case_id).first()
    if case is None:
        raise FraudCaseNotFoundError(f"No such fraud case {case_id!r}")
    if case.status != FraudCaseStatus.OPEN:
        raise FraudCaseAlreadyResolvedError(f"Case {case_id} is already {case.status.value}")

    case.status = status
    case.resolved_by = actor
    case.resolution_notes = notes
    case.resolved_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(case)
    log_event(
        db, actor=actor, action="risk.fraud_case_resolved",
        target=f"fraud_case:{case.id}", after={"status": status.value, "notes": notes},
    )
    if status == FraudCaseStatus.CLEARED:
        # Human reviewer found nothing - release the account back to
        # whatever tier it would be at on the merits (KYC/purchase history),
        # not straight back to REVIEW_REQUIRED's tighter limits.
        _force_set_risk_state(
            db, case.account_id, _compute_baseline_risk_state(db, case.account_id),
            actor=actor, reason=f"fraud case {case.id} cleared: {notes or 'no notes'}",
        )
    elif status == FraudCaseStatus.CONFIRMED:
        # Human reviewer confirmed real fraud - same outbound-blocking tier
        # as an automatic AUTO_SUSPEND_THRESHOLD suspension, even if this
        # particular account never crossed that score threshold itself.
        _transition_risk_state(
            db, case.account_id, AccountRiskState.SUSPENDED_FRAUD,
            actor=actor, reason=f"fraud case {case.id} confirmed: {notes or 'no notes'}",
        )
    return case


# Combined ordering across ALL AccountRiskState values (not just the three
# baseline tiers) - lets _transition_risk_state make one "only move forward"
# check regardless of whether the two states being compared are both
# baseline tiers or involve REVIEW_REQUIRED/SUSPENDED_FRAUD.
_RISK_STATE_RANK = {
    AccountRiskState.TRIAL_LOW: 0,
    AccountRiskState.TRIAL_VERIFIED: 1,
    AccountRiskState.PAID_NORMAL: 2,
    AccountRiskState.REVIEW_REQUIRED: 3,
    AccountRiskState.SUSPENDED_FRAUD: 4,
}


def _force_set_risk_state(db: Session, account_id: str, new_state: AccountRiskState, *, actor: str, reason: str) -> None:
    """Unconditional - the caller (a human resolving a fraud case, staff
    reinstating an account, or a staff manual override) has already made
    the actual decision; this just persists and audits it. Contrast with
    _transition_risk_state, which enforces "only move forward" for the
    automatic fraud-engine transitions."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None or account.risk_state == new_state:
        return
    before_state = account.risk_state
    account.risk_state = new_state
    db.commit()
    log_event(
        db, actor=actor, action="risk.account_risk_state_changed",
        target=f"account:{account_id}", reason=reason,
        before={"risk_state": before_state.value}, after={"risk_state": new_state.value},
    )


def _transition_risk_state(db: Session, account_id: str, new_state: AccountRiskState, *, actor: str, reason: str) -> None:
    """Automatic fraud-engine transition - only ever moves an account
    "forward" (see _RISK_STATE_RANK). Guards against, e.g., a KYC-approval
    step-up accidentally downgrading an account a later signal had already
    pushed to REVIEW_REQUIRED or SUSPENDED_FRAUD."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None or _RISK_STATE_RANK[new_state] <= _RISK_STATE_RANK[account.risk_state]:
        return
    _force_set_risk_state(db, account_id, new_state, actor=actor, reason=reason)


def _compute_baseline_risk_state(db: Session, account_id: str) -> AccountRiskState:
    """What tier an account belongs at on the merits alone - KYC/purchase
    history - ignoring any REVIEW_REQUIRED/SUSPENDED_FRAUD overlay a fraud
    signal may have added on top. Used to decide what to restore an account
    to once a human clears it (resolve_fraud_case) or reinstates its
    numbers (restore_risk_state_after_reinstatement), not during normal
    step-up (see step_up_risk_state_after_kyc_approval/_after_purchase,
    which only ever move an account forward one tier at a time)."""
    from app.compliance.models import ComplianceCase, ComplianceCaseStatus
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

    has_active_number = (
        db.query(PhoneNumber)
        .filter(PhoneNumber.account_id == account_id, PhoneNumber.status == PhoneNumberStatus.ACTIVE)
        .first()
        is not None
    )
    if has_active_number:
        return AccountRiskState.PAID_NORMAL

    has_kyc_approval = (
        db.query(ComplianceCase)
        .filter(ComplianceCase.account_id == account_id, ComplianceCase.status == ComplianceCaseStatus.APPROVED)
        .first()
        is not None
    )
    if has_kyc_approval:
        return AccountRiskState.TRIAL_VERIFIED

    return AccountRiskState.TRIAL_LOW


def step_up_risk_state_after_kyc_approval(db: Session, account_id: str) -> None:
    """Called once a ComplianceCase is approved (app.compliance.service.
    approve_case) - a trial account that's proven a real identity earns
    looser concurrent-call limits, even though it still hasn't paid for
    anything. Only ever promotes TRIAL_LOW -> TRIAL_VERIFIED (see
    _transition_risk_state) - a no-op for an account already at
    PAID_NORMAL or under REVIEW_REQUIRED/SUSPENDED_FRAUD."""
    _transition_risk_state(
        db, account_id, AccountRiskState.TRIAL_VERIFIED,
        actor="system:risk_engine", reason="KYC/compliance case approved",
    )


def step_up_risk_state_after_purchase(db: Session, account_id: str) -> None:
    """Called once a number purchase actually reaches ACTIVE
    (app.numbering.numbers.service.purchase_number) - the moment a trial
    account graduates into a real paying customer. Only ever promotes
    TRIAL_LOW/TRIAL_VERIFIED -> PAID_NORMAL (see _transition_risk_state) -
    a no-op for an account already PAID_NORMAL or under REVIEW_REQUIRED/
    SUSPENDED_FRAUD."""
    _transition_risk_state(
        db, account_id, AccountRiskState.PAID_NORMAL,
        actor="system:risk_engine", reason="first number purchase completed",
    )


def restore_risk_state_after_reinstatement(db: Session, account_id: str, *, actor: str) -> None:
    """Called from app.numbering.numbers.service.
    reactivate_numbers_for_account_by_staff - staff reactivating a
    risk-suspended account's numbers is exactly the kind of human decision
    that should also lift the account back out of SUSPENDED_FRAUD, onto
    whatever tier its KYC/purchase history actually supports (see
    _compute_baseline_risk_state), not leave it silently capped at 0
    concurrent calls after staff already decided it's fine."""
    _force_set_risk_state(
        db, account_id, _compute_baseline_risk_state(db, account_id),
        actor=actor, reason="numbers reinstated by staff after risk suspension",
    )


def set_account_risk_state(
    db: Session, account_id: str, *, state: AccountRiskState, actor: str, reason: str
) -> Account:
    """Staff manual override - the Production Readiness Standard doc's
    "Rule of Authority": a human can always override the automatic fraud
    engine's tier in either direction (tighten an account the model hasn't
    caught yet, or release one it flagged too aggressively), with a
    mandatory reason recorded the same way set_market_activation_status
    records one for a market-activation override. Unlike
    _transition_risk_state, this never checks rank - a staff override is
    the authority the rank check exists to defer to."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account {account_id!r}")
    _force_set_risk_state(db, account_id, state, actor=actor, reason=reason)
    db.refresh(account)
    return account


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
    (open_fraud_case_if_needed), not a hard signup gate - a coarse
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
