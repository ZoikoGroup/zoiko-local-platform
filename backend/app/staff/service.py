from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.models import (
    BillingPeriod,
    CatalogEntryStatus,
    Plan,
    PriceCatalogEntry,
    Subscription,
    SubscriptionStatus,
)
from app.core.security import hash_password, verify_password
from app.events.service import (
    publish_account_billing_classification_updated,
    publish_capability_granted,
    publish_capability_revoked,
)
from app.media.models import CallRecord
from app.numbering.identity.models import Account, User, UserRole
from app.numbering.numbers.models import PhoneNumber
from app.numbering.numbers.service import list_due_renewals as _list_due_renewals
from app.numbering.numbers.service import list_stuck_provisioning as _list_stuck_provisioning
from app.staff.models import PlatformStaff, PlatformStaffRole, StaffCapabilityGrant

# The capability that governs the grant/revoke endpoints below. Protected
# against ever reaching zero grants (see revoke_capability) - if no role
# could manage the matrix anymore, fixing that would need direct database
# access instead of the UI meant to make this a data change, not a
# redeploy.
MATRIX_MANAGEMENT_CAPABILITY = "staff.manage_capabilities"

# Computed once at import time - see authenticate_staff's docstring for why
# this needs to exist at all (same rationale as identity/service.py's
# _DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY).
_DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY = hash_password("zoiko-local-timing-safety-dummy")


class LastGrantRemovalError(Exception):
    """Raised when revoking a grant would leave MATRIX_MANAGEMENT_CAPABILITY
    with zero roles able to manage the matrix at all - an unrecoverable
    lockout short of direct database access."""


def create_staff(db: Session, email: str, password: str, role: PlatformStaffRole) -> PlatformStaff:
    """role has no default - there's no public staff signup endpoint (staff
    are provisioned internally, see app/seed.py), so every call site must
    consciously pick a role rather than silently inheriting a default."""
    existing = db.query(PlatformStaff).filter(PlatformStaff.email == email).first()
    if existing:
        raise ValueError("A staff account with this email already exists")

    staff = PlatformStaff(email=email, hashed_password=hash_password(password), role=role)
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


class StaffEmailAlreadyExistsError(Exception):
    """Raised creating a staff account whose email is already taken."""


class StaffNotFoundError(Exception):
    """Raised when a staff_id doesn't match any platform_staff row."""


class LastActiveSuperAdminError(Exception):
    """Raised deactivating the only remaining active SUPER_ADMIN - an
    unrecoverable lockout short of direct database access, same class of
    guard as LastGrantRemovalError above for staff.manage_capabilities."""


def list_staff_team(db: Session) -> list[PlatformStaff]:
    """Every internal staff account - any staff role can view who else has
    console access (diagnostic, same "GET is open" posture as list_
    accounts_overview); creating or deactivating one is the sensitive
    action, gated separately by staff.manage_staff_accounts."""
    return db.query(PlatformStaff).order_by(PlatformStaff.created_at.asc()).all()


def create_staff_member(
    db: Session, *, email: str, password: str, role: PlatformStaffRole, actor: str
) -> PlatformStaff:
    """SUPER_ADMIN-gated (staff.manage_staff_accounts) console entry point
    for provisioning a teammate - the only real gap this whole capability
    system had: bootstrap_initial_super_admin creates exactly one account
    at first boot and nothing since then could create a second one short
    of direct database access, which also meant the billing maker-checker
    flow (request vs. approve, self-approval blocked) could never actually
    run with only one SUPER_ADMIN to ever hold both roles.

    Thin wrapper around create_staff (the same function app.seed and
    bootstrap_initial_super_admin already use) that adds the audit trail
    a console-driven creation needs - those two callers log their own
    event under a different actor shape (system_bootstrap / the seed
    script), so create_staff itself deliberately stays bare rather than
    hardcoding one audit shape for every caller."""
    if db.query(PlatformStaff).filter(PlatformStaff.email == email).first() is not None:
        raise StaffEmailAlreadyExistsError(f"A staff account with email {email!r} already exists")
    staff = create_staff(db, email=email, password=password, role=role)
    log_event(
        db, actor=actor, action="staff.created", target=f"platform_staff:{staff.id}",
        after={"email": email, "role": role.value},
    )
    return staff


def _get_staff_or_raise(db: Session, staff_id: str) -> PlatformStaff:
    staff = db.query(PlatformStaff).filter(PlatformStaff.id == staff_id).first()
    if staff is None:
        raise StaffNotFoundError(f"No such staff account: {staff_id!r}")
    return staff


def deactivate_staff_member(db: Session, staff_id: str, *, actor: str) -> PlatformStaff:
    """Revokes console access without deleting the row - every past action
    they took must stay attributable to a real account for the audit
    trail. Takes effect on their very next request: get_current_staff
    (core/deps.py) re-checks is_active on every call and deliberately
    never caches an inactive row, so there's no stale-cache window to
    wait out the way there would be for a role change.

    Blocked from ever dropping the platform to zero active SUPER_ADMINs -
    same unrecoverable-lockout class LastGrantRemovalError already guards
    against for staff.manage_capabilities, just for "nobody left who can
    manage staff/billing/kill-switches at all" rather than "nobody left
    who can edit the access matrix"."""
    staff = _get_staff_or_raise(db, staff_id)
    if staff.role == PlatformStaffRole.SUPER_ADMIN and staff.is_active:
        other_active_admins = (
            db.query(PlatformStaff)
            .filter(
                PlatformStaff.role == PlatformStaffRole.SUPER_ADMIN,
                PlatformStaff.is_active.is_(True),
                PlatformStaff.id != staff_id,
            )
            .count()
        )
        if other_active_admins == 0:
            raise LastActiveSuperAdminError(
                "Cannot deactivate the only active Super Admin - promote another account first."
            )

    staff.is_active = False
    db.commit()
    db.refresh(staff)
    log_event(
        db, actor=actor, action="staff.deactivated", target=f"platform_staff:{staff.id}",
        before={"is_active": True}, after={"is_active": False},
    )
    return staff


def reactivate_staff_member(db: Session, staff_id: str, *, actor: str) -> PlatformStaff:
    staff = _get_staff_or_raise(db, staff_id)
    staff.is_active = True
    db.commit()
    db.refresh(staff)
    log_event(
        db, actor=actor, action="staff.reactivated", target=f"platform_staff:{staff.id}",
        before={"is_active": False}, after={"is_active": True},
    )
    return staff


def bootstrap_initial_super_admin(db: Session) -> PlatformStaff | None:
    """Called once from main.py's lifespan startup, every boot. app.seed
    refuses to run outside development on purpose (it hardcodes real
    checked-into-repo demo credentials) - which left production/staging
    with genuinely no way to create the first staff account at all
    (platform_staff has no public signup route by design, see routes.py's
    top-of-file comment). This is that missing bootstrap: idempotent
    (no-ops the instant any staff row exists, so it's safe to call on
    every restart) and driven entirely by operator-supplied env vars
    (INITIAL_SUPER_ADMIN_EMAIL/_PASSWORD) rather than a hardcoded
    password, so it can run in any environment without becoming a
    standing backdoor. Returns None (does nothing) if either env var is
    unset, or if a staff account already exists."""
    if db.query(PlatformStaff).first() is not None:
        return None
    from app.core.config import settings

    email = settings.initial_super_admin_email
    password = settings.initial_super_admin_password
    if not email or not password:
        return None

    staff = create_staff(db, email=email, password=password, role=PlatformStaffRole.SUPER_ADMIN)
    log_event(
        db, actor="system_bootstrap", action="staff.bootstrapped", target=f"platform_staff:{staff.id}",
        after={"email": email, "role": PlatformStaffRole.SUPER_ADMIN.value},
    )
    return staff


def authenticate_staff(db: Session, email: str, password: str) -> PlatformStaff | None:
    """Always runs verify_password against SOMETHING - a real hash, or
    _DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY for a nonexistent/inactive staff
    account - rather than short-circuiting before ever calling it. Without
    this, a login attempt for an email that isn't a staff account (or is
    deactivated) returns measurably faster than a wrong-password attempt
    against a real, active one, leaking which emails are valid staff
    accounts through timing alone - a more sensitive thing to leak here
    than on the customer side, since staff accounts include SUPER_ADMIN."""
    staff = db.query(PlatformStaff).filter(PlatformStaff.email == email).first()
    hashed_password = staff.hashed_password if staff else _DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY
    password_matches = verify_password(password, hashed_password)
    if not staff or not staff.is_active or not password_matches:
        return None
    return staff


def list_accounts_overview(db: Session) -> list[dict]:
    """Cross-account visibility for Zoiko ops - who owns this account, how
    many team members and numbers it has. Staff-only (see routes.py:
    get_current_staff), same rationale as compliance's list_all_cases: a
    customer must never see another customer's account, but ops legitimately
    needs to look one up without a database console.
    """
    member_counts = dict(
        db.query(User.account_id, func.count(User.id)).group_by(User.account_id).all()
    )
    number_counts = dict(
        db.query(PhoneNumber.account_id, func.count(PhoneNumber.id)).group_by(PhoneNumber.account_id).all()
    )
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.role == UserRole.OWNER).all()
    }

    accounts = db.query(Account).order_by(Account.created_at.desc()).all()
    return [
        {
            "id": account.id,
            "name": account.name,
            "account_type": account.account_type,
            "owner_email": owners.get(account.id),
            "member_count": member_counts.get(account.id, 0),
            "number_count": number_counts.get(account.id, 0),
            "billing_classification": account.billing_classification,
            "billing_source": account.billing_source,
            "is_test": account.is_test,
            "legal_hold": account.legal_hold,
            "legal_hold_reference": account.legal_hold_reference,
            "created_at": account.created_at,
        }
        for account in accounts
    ]


def get_platform_call_metrics(db: Session, *, window_days: int = 30) -> dict:
    """Platform-wide call volume (every account, not one) - built for the
    Super Admin Overview dashboard, since nothing in this codebase
    aggregated CallRecord across accounts before now (the closest
    precedent, run_zoikonex_reconciliation, counts rows for drift
    detection, not for a volume report). Same "GET is diagnostic, open to
    any staff role" posture as list_accounts_overview above - the route
    exposing this is open to any staff, only the frontend UI restricts it
    to SUPER_ADMIN. Scoped to a rolling window rather than all-time so the
    number stays meaningful as volume grows and the query doesn't turn
    into a full-table scan a year from now."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    rows = (
        db.query(CallRecord.status, func.count(CallRecord.id), func.coalesce(func.sum(CallRecord.duration), 0))
        .filter(CallRecord.created_at >= cutoff)
        .group_by(CallRecord.status)
        .all()
    )
    total_calls = sum(count for _, count, _ in rows)
    total_seconds = sum(seconds for _, _, seconds in rows)
    return {
        "window_days": window_days,
        "total_calls": total_calls,
        "total_minutes": round(total_seconds / 60, 1),
        "by_status": sorted(
            [{"status": status, "count": count} for status, count, _ in rows],
            key=lambda r: -r["count"],
        ),
    }


def get_platform_billing_metrics(db: Session) -> dict:
    """Platform-wide subscription/revenue snapshot - Super Admin dashboard
    only (same open-GET-restricted-UI posture as get_platform_call_metrics
    above). estimated_mrr_minor_units is a PLANNING ESTIMATE, not a real
    revenue-recognition figure: it prices each active subscription off the
    approved, non-placeholder price catalog (PriceCatalogEntry - see its
    docstring on why placeholder rows are excluded) and normalizes annual
    subscriptions to a monthly-equivalent (amount / 12) so mixed billing
    periods sum to one comparable number - it does not account for
    proration, discounts, failed payments, or mid-cycle plan changes.
    A subscription whose plan+period has no matching ACTIVE catalog entry
    contributes to total_active_subscriptions/by_plan but not to the MRR
    sum (same "don't invent a price" discipline PriceCatalogEntry's
    docstring already establishes elsewhere in this codebase)."""
    active_subs = (
        db.query(Subscription.plan_code, Subscription.billing_period, func.count(Subscription.id))
        .filter(Subscription.status == SubscriptionStatus.ACTIVE)
        .group_by(Subscription.plan_code, Subscription.billing_period)
        .all()
    )

    # market="GLOBAL": the only market that exists today (see
    # PriceCatalogEntry's docstring) - filtered explicitly so a future
    # market-specific row can never silently collide with this one in the
    # dict below rather than by accident of query ordering.
    prices = {
        (entry.plan_code, entry.billing_period): entry.amount_minor_units
        for entry in db.query(PriceCatalogEntry).filter(
            PriceCatalogEntry.status == CatalogEntryStatus.ACTIVE,
            PriceCatalogEntry.is_placeholder.is_(False),
            PriceCatalogEntry.market == "GLOBAL",
        )
    }
    plan_names = {code: name for code, name in db.query(Plan.plan_code, Plan.name).all()}

    by_plan: dict[str, int] = {}
    mrr_minor_units = 0.0
    for plan_code, period, count in active_subs:
        by_plan[plan_code] = by_plan.get(plan_code, 0) + count
        amount = prices.get((plan_code, period))
        if amount is None:
            continue
        monthly_equivalent = amount if period == BillingPeriod.MONTHLY else amount / 12
        mrr_minor_units += monthly_equivalent * count

    return {
        "total_active_subscriptions": sum(by_plan.values()),
        "estimated_mrr_minor_units": round(mrr_minor_units),
        "currency_code": "USD",
        "by_plan": sorted(
            [
                {"plan_code": code, "plan_name": plan_names.get(code, code), "count": count}
                for code, count in by_plan.items()
            ],
            key=lambda r: -r["count"],
        ),
    }


class AccountNotFoundError(Exception):
    """Raised when an account id doesn't exist."""


def get_account_overview(db: Session, account_id: str) -> dict:
    """Single-account counterpart tois  list_accounts_overview - avoids
    pulling every account just to return one, e.g. right after a billing-
    classification update."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    return {
        "id": account.id,
        "name": account.name,
        "account_type": account.account_type,
        "owner_email": owner.email if owner else None,
        "member_count": db.query(User).filter(User.account_id == account_id).count(),
        "number_count": db.query(PhoneNumber).filter(PhoneNumber.account_id == account_id).count(),
        "billing_classification": account.billing_classification,
        "billing_source": account.billing_source,
        "is_test": account.is_test,
        "legal_hold": account.legal_hold,
        "legal_hold_reference": account.legal_hold_reference,
        "created_at": account.created_at,
    }


def update_account_billing_classification(
    db: Session, account_id: str, *, billing_classification, billing_source, actor: str
) -> Account:
    """Commercial Billing Operating Standard doc's P0 blocker: every
    account needs a real billing_classification/billing_source, settable
    by staff for the non-default cases (marking an account DEMO, SANDBOX,
    a PARTNER_SPONSORED deal, etc.) - see Account model's docstring for
    why the public signup path only ever creates COMMERCIAL_STANDALONE."""
    account = db.query(Account).filter( Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")

    before = {"billing_classification": account.billing_classification.value, "billing_source": account.billing_source.value}
    account.billing_classification = billing_classification
    account.billing_source = billing_source
    db.commit()
    db.refresh(account)
    log_event(
        db, actor=actor, action="account.billing_classification_updated", target=f"account:{account.id}",
        account_id=account.id,
        before=before,
        after={"billing_classification": billing_classification.value, "billing_source": billing_source.value},
    )
    publish_account_billing_classification_updated(
        account_id, billing_classification=billing_classification.value, billing_source=billing_source.value,
    )
    return account


def set_account_test_flag(db: Session, account_id: str, *, is_test: bool, actor: str, reason: str) -> Account:
    """Backs the accounts.manage_test_flag capability (granted in migration
    db8d0f0b2e05, which shipped no route/service function for it - this is
    that missing piece). is_test bypasses the CONTROLLED_BETA/INTERNAL_TEST
    market-activation gate (see app.numbering.numbers.service.
    _assert_market_activated) and blocks real ZoikoNex/Stripe billing (see
    app.billing.service.assert_not_test_account) - a platform-wide decision
    a SUPER_ADMIN makes deliberately, not a routine support toggle, so
    `reason` is mandatory for the audit trail (same bar as
    set_market_activation_status's reason)."""
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")

    previous = account.is_test
    account.is_test = is_test
    db.commit()
    db.refresh(account)
    log_event(
        db, actor=actor, action="account.test_flag_updated", target=f"account:{account.id}",
        account_id=account.id,
        reason=reason, before={"is_test": previous}, after={"is_test": is_test},
    )
    return account


class LegalHoldRequiresReferenceError(Exception):
    """Raised when activating a legal hold with no reference - a real
    case/matter reference is required (same "record a reference, not just
    a reason" discipline as the market-activation legal sign-off), not a
    free-text justification."""


def set_account_legal_hold(
    db: Session, account_id: str, *, on: bool, reference: str | None, actor: str
) -> Account:
    """Architecture doc §10 "legal hold model for business customers" -
    while active, app.retention.service's purge sweeps AND
    erase_account_data skip/refuse every recording/voicemail (or the whole
    erasure) on this account regardless of how overdue its normal
    retention window is. Staff-only (same SUPER_ADMIN bar as the test-flag
    toggle above) - this can override a customer's own configured
    retention preference, which is exactly the kind of override that needs
    a real audit trail, not a routine support action."""
    if on and not reference:
        raise LegalHoldRequiresReferenceError("Activating a legal hold requires a real case/matter reference")
    account = db.query(Account).filter(Account.id == account_id).first()
    if account is None:
        raise AccountNotFoundError(f"No such account: {account_id!r}")
    previous = {"legal_hold": account.legal_hold, "legal_hold_reference": account.legal_hold_reference}
    account.legal_hold = on
    account.legal_hold_reference = reference if on else None
    db.commit()
    db.refresh(account)
    log_event(
        db, actor=actor, action="account.legal_hold_changed", target=f"account:{account.id}",
        account_id=account.id,
        before=previous, after={"legal_hold": account.legal_hold, "legal_hold_reference": account.legal_hold_reference},
    )
    return account


def list_stuck_provisioning(db: Session) -> list[dict]:
    """Staff recovery queue - numbers stranded mid-purchase by a process
    crash, joined with enough account context to act without a second
    lookup (same pattern as list_accounts_overview / compliance's
    list_all_cases / porting's list_all_porting_requests)."""
    numbers = _list_stuck_provisioning(db)
    account_ids = {n.account_id for n in numbers}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.account_id.in_(account_ids), User.role == UserRole.OWNER).all()
    }
    return [
        {
            "id": n.id,
            "e164": n.e164,
            "country": n.country,
            "status": n.status,
            "account_id": n.account_id,
            "account_name": accounts[n.account_id].name if n.account_id in accounts else None,
            "account_owner_email": owners.get(n.account_id),
            "provisioning_started_at": n.provisioning_started_at,
            "last_provisioning_error_code": n.last_provisioning_error_code,
            "provisioning_attempt_count": n.provisioning_attempt_count,
        }
        for n in numbers
    ]


def list_due_renewals(db: Session) -> list[dict]:
    """Staff worklist of numbers past their renewal date - see
    app.numbering.numbers.service.list_due_renewals's docstring on why
    this is a manual worklist rather than automated billing. Same
    join-in-context shape as list_stuck_provisioning."""
    numbers = _list_due_renewals(db)
    account_ids = {n.account_id for n in numbers}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.account_id.in_(account_ids), User.role == UserRole.OWNER).all()
    }
    return [
        {
            "id": n.id,
            "e164": n.e164,
            "country": n.country,
            "status": n.status,
            "account_id": n.account_id,
            "account_name": accounts[n.account_id].name if n.account_id in accounts else None,
            "account_owner_email": owners.get(n.account_id),
            "next_renewal_at": n.next_renewal_at,
        }
        for n in numbers
    ]


def list_access_matrix(db: Session) -> list[dict]:
    """The role x capability grid, grouped by capability - one row per
    capability with every role currently granted it (see
    app.staff.models.StaffCapabilityGrant's docstring). Grouped in Python
    rather than SQL since the grid is small (a dozen or so capabilities)
    and grouping here keeps the response shape simple for the staff
    console table."""
    grants = db.query(StaffCapabilityGrant).order_by(StaffCapabilityGrant.capability).all()
    by_capability: dict[str, list[str]] = {}
    for grant in grants:
        by_capability.setdefault(grant.capability, []).append(grant.role.value)
    return [
        {"capability": capability, "roles": sorted(roles)}
        for capability, roles in sorted(by_capability.items())
    ]


def grant_capability(db: Session, *, capability: str, role: PlatformStaffRole, actor: str) -> None:
    """Idempotent - granting a role that already has this capability is a
    no-op, not a duplicate-row error."""
    existing = (
        db.query(StaffCapabilityGrant)
        .filter(StaffCapabilityGrant.capability == capability, StaffCapabilityGrant.role == role)
        .first()
    )
    if existing is not None:
        return

    db.add(StaffCapabilityGrant(capability=capability, role=role))
    db.commit()
    log_event(
        db, actor=actor, action="staff.capability_granted", target=f"capability:{capability}",
        after={"role": role.value},
    )
    publish_capability_granted(capability=capability, role=role.value, actor=actor)


def revoke_capability(db: Session, *, capability: str, role: PlatformStaffRole, actor: str) -> None:
    """Idempotent - revoking a grant that doesn't exist is a no-op.
    Refuses to remove the last remaining grant of
    MATRIX_MANAGEMENT_CAPABILITY (see LastGrantRemovalError's docstring) -
    every other capability, including one this exact staff member relies
    on for something else, can be revoked down to zero roles."""
    grant = (
        db.query(StaffCapabilityGrant)
        .filter(StaffCapabilityGrant.capability == capability, StaffCapabilityGrant.role == role)
        .first()
    )
    if grant is None:
        return

    if capability == MATRIX_MANAGEMENT_CAPABILITY:
        remaining = (
            db.query(StaffCapabilityGrant)
            .filter(StaffCapabilityGrant.capability == capability, StaffCapabilityGrant.id != grant.id)
            .count()
        )
        if remaining == 0:
            raise LastGrantRemovalError(
                f"Cannot revoke the last role able to manage the capability matrix ({role.value})"
            )

    db.delete(grant)
    db.commit()
    log_event(
        db, actor=actor, action="staff.capability_revoked", target=f"capability:{capability}",
        before={"role": role.value},
    )
    publish_capability_revoked(capability=capability, role=role.value, actor=actor)


def search_numbers(db: Session, query: str) -> list[dict]:
    """Cross-account number lookup for ops - a support agent has a number or
    a Twilio SID a customer gave them and needs to find which account owns
    it, without a database console. Same join-in-context shape as the other
    staff list views. Capped at 50 - this is a lookup tool for a specific
    number, not a bulk export."""
    pattern = f"%{query}%"
    numbers = (
        db.query(PhoneNumber)
        .filter(or_(PhoneNumber.e164.ilike(pattern), PhoneNumber.provider_sid.ilike(pattern)))
        .order_by(PhoneNumber.created_at.desc())
        .limit(50)
        .all()
    )
    account_ids = {n.account_id for n in numbers}
    accounts = {a.id: a for a in db.query(Account).filter(Account.id.in_(account_ids)).all()}
    owners = {
        u.account_id: u.email
        for u in db.query(User).filter(User.account_id.in_(account_ids), User.role == UserRole.OWNER).all()
    }
    return [
        {
            "id": n.id,
            "e164": n.e164,
            "country": n.country,
            "status": n.status,
            "provider_sid": n.provider_sid,
            "account_id": n.account_id,
            "account_name": accounts[n.account_id].name if n.account_id in accounts else None,
            "account_owner_email": owners.get(n.account_id),
            "created_at": n.created_at,
        }
        for n in numbers
    ]
