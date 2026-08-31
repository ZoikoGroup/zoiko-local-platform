from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing.service import assert_entitlement, assert_seat_quota_available
from app.events.service import publish_account_created
from app.core.security import (
    create_access_token,
    decode_access_token,
    generate_mfa_secret,
    hash_password,
    mfa_provisioning_uri,
    password_fingerprint,
    verify_password,
    verify_totp_code,
)
from app.notifications.service import (
    notify_account_activated,
    notify_administrator_added,
    notify_administrator_removed,
    notify_email_verification_requested,
    notify_mfa_disabled,
    notify_mfa_enabled,
    notify_password_changed,
    notify_password_reset_requested,
    notify_team_member_added,
)
from app.numbering.identity.models import Account, AccountType, User, UserRole
from app.risk.service import check_fingerprint_on_signup

PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 30
# Email Communications System doc §5.3's normative token lifetime for
# ZLOC-EM-AUTH-001 "Verify Email Address": 24 hours, single use.
EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES = 60 * 24

# Computed once at import time (a one-time bcrypt cost, same as hashing any
# real password) - see authenticate_user's docstring for why this needs to
# exist at all.
_DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY = hash_password("zoiko-local-timing-safety-dummy")


def create_account_with_owner(
    db: Session, account_name: str, account_type: str, email: str, password: str,
    device_fingerprint_hash: str | None = None,
) -> User:
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError("An account with this email already exists")

    account = Account(name=account_name, account_type=AccountType(account_type))
    db.add(account)
    db.flush()  # assigns account.id without committing yet

    user = User(
        account_id=account.id,
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.OWNER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_event(
        db,
        actor=user.id,
        action="account.signup",
        target=f"account:{account.id}",
        after={"account_id": account.id, "user_id": user.id, "email": user.email, "role": user.role},
    )
    publish_account_created(account.id, user_id=user.id, email=user.email, account_type=account_type)
    notify_account_activated(db, account_id=account.id, user_email=user.email)
    # Architecture doc §5 "Fraud and Risk: device fingerprinting" - detection
    # only, never blocks signup itself (see check_fingerprint_on_signup's
    # docstring). Optional: a client that sends no fingerprint header is a
    # no-op here, not an error.
    check_fingerprint_on_signup(db, fingerprint_hash=device_fingerprint_hash, account_id=account.id)
    send_email_verification(db, user)
    return user


def send_email_verification(db: Session, user: User) -> None:
    """Production Readiness Standard doc §5 "Identity" - a fresh signup
    starts email_verified=False; this issues the ZLOC-EM-AUTH-001 link.
    Shared by create_account_with_owner (first send) and
    resend_email_verification below (re-send after the 24h link expires)."""
    token = create_access_token(
        subject=user.id, scope="email_verification", expire_minutes=EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES,
    )
    notify_email_verification_requested(db, account_id=user.account_id, user_email=user.email, token=token)


class InvalidVerificationTokenError(Exception):
    """Raised for an expired, malformed, or already-used email verification
    token - same "one generic error, don't let the caller distinguish why"
    posture as InvalidResetTokenError."""


def verify_email(db: Session, token: str) -> User:
    payload = decode_access_token(token)
    if payload is None or payload.get("scope") != "email_verification":
        raise InvalidVerificationTokenError("This verification link is invalid or has expired.")

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if user is None:
        raise InvalidVerificationTokenError("This verification link is invalid or has expired.")

    # Naturally single-use: verifying an already-verified account is a
    # harmless no-op, not an error - a customer who clicks an old link
    # again (or double-clicks) after already verifying shouldn't see a
    # confusing failure.
    if not user.email_verified:
        user.email_verified = True
        db.commit()
        db.refresh(user)
        log_event(db, actor=user.id, action="user.email_verified", target=f"user:{user.id}")
    return user


def resend_email_verification(db: Session, email: str) -> None:
    """Same anti-enumeration posture as request_password_reset - always
    succeeds from the caller's perspective regardless of whether the email
    matches an account or is already verified."""
    user = db.query(User).filter(User.email == email).first()
    if user is None or user.email_verified:
        return
    send_email_verification(db, user)


def authenticate_user(db: Session, email: str, password: str) -> User | None:
    user = db.query(User).filter(User.email == email).first()
    # Google-only accounts have no password at all - never match, and
    # never pass None into verify_password (it expects a real hash string).
    # Always runs verify_password against SOMETHING - a real hash, or
    # _DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY when there's no user or no
    # password to compare against - rather than short-circuiting before
    # ever calling it. bcrypt verification is the dominant cost of this
    # function; skipping it for a nonexistent email would make that lookup
    # return measurably faster than a wrong-password attempt against a
    # real account, leaking whether an email is registered through timing
    # alone even though the error is already generic (the same account-
    # enumeration surface request_password_reset's identical response
    # already guards against, just via timing instead of content).
    hashed_password = user.hashed_password if user and user.hashed_password else _DUMMY_PASSWORD_HASH_FOR_TIMING_SAFETY
    password_matches = verify_password(password, hashed_password)
    if not user or user.hashed_password is None or not password_matches:
        return None

    # Only log "user.login" here if this IS the complete login. If MFA is
    # enabled, the real login isn't done yet - complete_mfa_login() logs
    # it once the code is verified, so we don't log an incomplete login.
    if not user.mfa_enabled:
        log_event(db, actor=user.id, action="user.login", target=f"user:{user.id}")
    return user


class InvalidResetTokenError(Exception):
    """Raised for an expired, malformed, or already-used password reset
    token - deliberately the same error/message for all three (see
    reset_password's docstring) so a caller can't distinguish them."""


def request_password_reset(db: Session, email: str) -> None:
    """Always succeeds from the caller's perspective regardless of whether
    the email matches an account - the route never reveals which (see
    routes.py), the standard anti-account-enumeration posture for this kind
    of endpoint. Silently no-ops for an unknown email; still sends for a
    Google-only account (hashed_password is None) - completing the reset
    sets one, which is a reasonable bonus (adds password login) rather than
    a bug."""
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        return

    token = create_access_token(
        subject=user.id,
        scope="password_reset",
        expire_minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES,
    )
    fingerprint = password_fingerprint(user.hashed_password or "")
    notify_password_reset_requested(db, account_id=user.account_id, user_email=user.email, token=f"{token}.{fingerprint}")
    log_event(db, actor=user.id, action="user.password_reset_requested", target=f"user:{user.id}")


def reset_password(db: Session, token: str, new_password: str) -> User:
    """token is `<jwt>.<fingerprint>` (see request_password_reset) - the JWT
    proves who requested it and that it hasn't expired; the fingerprint,
    checked against the user's CURRENT hashed_password, proves the password
    hasn't already been changed since this token was issued (the one-time-
    use mechanism - see password_fingerprint's docstring). Both failure
    modes raise the same InvalidResetTokenError with the same message,
    so a caller can't distinguish "expired" from "already used" from
    "malformed" - none of that should be observable to whoever holds the
    token."""
    try:
        jwt_part, fingerprint = token.rsplit(".", 1)
    except ValueError:
        raise InvalidResetTokenError("This password reset link is invalid or has expired.")

    payload = decode_access_token(jwt_part)
    if payload is None or payload.get("scope") != "password_reset":
        raise InvalidResetTokenError("This password reset link is invalid or has expired.")

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if user is None or password_fingerprint(user.hashed_password or "") != fingerprint:
        raise InvalidResetTokenError("This password reset link is invalid or has expired.")

    user.hashed_password = hash_password(new_password)
    db.commit()
    db.refresh(user)

    from app.core.deps import invalidate_cached_user

    invalidate_cached_user(user.id)
    log_event(db, actor=user.id, action="user.password_reset_completed", target=f"user:{user.id}")
    notify_password_changed(db, account_id=user.account_id, user_email=user.email)
    return user


def find_or_create_user_from_google(db: Session, email: str, name: str | None) -> tuple[User, bool]:
    """Logs in an existing account by email, or creates a brand new
    individual account + Google-only User (no password) if none exists.
    Returns (user, created) - the caller needs to tell the two apart: an
    EXISTING customer clicking "Sign in with Google" on the signup page
    must never be routed through post-signup plan selection (choose_plan
    calls change_plan for real - silently reassigning an existing paid
    account's real subscription would be a genuine bug, not cosmetic)."""
    user = db.query(User).filter(User.email == email).first()
    if user:
        log_event(db, actor=user.id, action="user.login", target=f"user:{user.id}", reason="google")
        return user, False

    account = Account(name=name or email, account_type=AccountType.INDIVIDUAL)
    db.add(account)
    db.flush()

    user = User(
        account_id=account.id,
        email=email,
        hashed_password=None,
        role=UserRole.OWNER,
        # Google already proved ownership of this address via its own
        # id_token email_verified claim (checked in the /auth/google route
        # before this function is ever called) - making them verify again
        # here would be pointless.
        email_verified=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    log_event(
        db,
        actor=user.id,
        action="account.signup",
        target=f"account:{account.id}",
        reason="google",
        after={"account_id": account.id, "user_id": user.id, "email": user.email, "role": user.role},
    )
    publish_account_created(account.id, user_id=user.id, email=user.email, account_type=AccountType.INDIVIDUAL.value)
    return user, True


def add_team_member(
    db: Session, *, account_id: str, email: str, password: str, role: str, actor: str
) -> User:
    if role == UserRole.OWNER.value:
        raise ValueError("Cannot add a second owner - there is exactly one owner per account")
    if role not in (UserRole.ADMIN.value, UserRole.MEMBER.value, UserRole.VIEWER.value):
        raise ValueError("role must be 'admin', 'member', or 'viewer'")

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError("A user with this email already exists")

    # ZL-COM-ENT-001 §7 matrix: "Team members, roles & number assignment:
    # No (Starter) / Yes (Business+)" - Starter has no team-member
    # capability at all, a distinct check from assert_seat_quota_available
    # below (a numeric cap that only applies to plans that HAVE the
    # feature). Real gap fix: previously a Starter account could add up to
    # its Plan.max_team_seats members (5, per seed data) with no gate on
    # the team feature itself at all.
    assert_entitlement(db, account_id, "team.enabled")
    assert_seat_quota_available(db, account_id)

    member = User(
        account_id=account_id,
        email=email,
        hashed_password=hash_password(password),
        role=UserRole(role),
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    log_event(
        db,
        actor=actor,
        action="team.member_added",
        target=f"user:{member.id}",
        after={"user_id": member.id, "email": member.email, "role": member.role},
    )

    account = db.query(Account).filter(Account.id == account_id).first()
    if account is not None:
        notify_team_member_added(
            db, account_id=account_id, member_email=member.email, account_name=account.name, role=member.role
        )
        if member.role == UserRole.ADMIN:
            owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
            if owner is not None:
                notify_administrator_added(
                    db, account_id=account_id, account_email=owner.email, organization_name=account.name,
                    new_admin_display_name=member.email,
                )

    return member


def list_team_members(db: Session, account_id: str) -> list[User]:
    return db.query(User).filter(User.account_id == account_id).order_by(User.created_at).all()


def remove_team_member(db: Session, *, account_id: str, user_id: str, actor: str) -> None:
    member = db.query(User).filter(User.id == user_id).first()
    if member is None or member.account_id != account_id:
        raise ValueError("No such team member on this account")
    if member.role == UserRole.OWNER:
        raise ValueError("Cannot remove the account owner")

    # Capture values before delete+commit - the ORM object is expired/
    # detached afterward since the underlying row no longer exists.
    removed_email, removed_role = member.email, member.role

    db.delete(member)
    db.commit()

    log_event(
        db,
        actor=actor,
        action="team.member_removed",
        target=f"user:{user_id}",
        before={"user_id": user_id, "email": removed_email, "role": removed_role},
    )

    if removed_role == UserRole.ADMIN:
        owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
        account = db.query(Account).filter(Account.id == account_id).first()
        if owner is not None and account is not None:
            notify_administrator_removed(
                db, account_id=account_id, account_email=owner.email, organization_name=account.name,
                removed_admin_display_name=removed_email,
            )


def start_mfa_setup(db: Session, user: User) -> tuple[str, str]:
    """Generates a new pending TOTP secret (not yet enabled). Returns
    (secret, otpauth_uri) for the caller to show as text/QR code.

    `user` comes from a route's get_current_user dependency, which may
    return a Redis-cache-deserialized instance that was never loaded
    through `db` - mutating and committing that directly is a silent
    no-op (the session's unit-of-work never sees the change) rather than
    a real update. db.merge() reattaches it to this session first."""
    from app.core.deps import invalidate_cached_user

    user = db.merge(user)
    secret = generate_mfa_secret()
    user.mfa_secret = secret
    user.mfa_enabled = False  # any previous enabled state is cleared until re-confirmed
    db.commit()
    invalidate_cached_user(user.id)
    return secret, mfa_provisioning_uri(secret, user.email)


def enable_mfa(db: Session, user: User, code: str, actor: str) -> None:
    """See start_mfa_setup's docstring for why db.merge(user) is needed
    before any mutation - `user` may be a detached, cache-deserialized
    instance, not one loaded through this session."""
    from app.core.deps import invalidate_cached_user

    if not user.mfa_secret:
        raise ValueError("Call /auth/mfa/setup first")
    if not verify_totp_code(user.mfa_secret, code):
        raise ValueError("Invalid code")

    user = db.merge(user)
    user.mfa_enabled = True
    db.commit()
    invalidate_cached_user(user.id)
    log_event(db, actor=actor, action="mfa.enabled", target=f"user:{user.id}")
    notify_mfa_enabled(db, account_id=user.account_id, user_email=user.email)


def disable_mfa(db: Session, user: User, code: str, actor: str) -> None:
    """See start_mfa_setup's docstring for why db.merge(user) is needed
    before any mutation - `user` may be a detached, cache-deserialized
    instance, not one loaded through this session."""
    from app.core.deps import invalidate_cached_user

    if not user.mfa_enabled or not user.mfa_secret:
        raise ValueError("MFA is not enabled")
    if not verify_totp_code(user.mfa_secret, code):
        raise ValueError("Invalid code")

    user = db.merge(user)
    user.mfa_secret = None
    user.mfa_enabled = False
    db.commit()
    invalidate_cached_user(user.id)
    log_event(db, actor=actor, action="mfa.disabled", target=f"user:{user.id}")
    notify_mfa_disabled(db, account_id=user.account_id, user_email=user.email)


def set_phone_number(db: Session, user: User, phone_number: str | None) -> User:
    """See start_mfa_setup's docstring for why db.merge(user) is needed
    before any mutation - `user` may be a detached, cache-deserialized
    instance, not one loaded through this session."""
    from app.core.deps import invalidate_cached_user

    user = db.merge(user)
    before = user.phone_number
    user.phone_number = phone_number
    db.commit()
    db.refresh(user)
    invalidate_cached_user(user.id)
    log_event(
        db, actor=user.id, action="user.phone_number_updated", target=f"user:{user.id}",
        before={"phone_number": before}, after={"phone_number": phone_number},
    )
    return user


def complete_mfa_login(db: Session, user_id: str, code: str) -> User | None:
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.mfa_enabled or not user.mfa_secret:
        return None
    if not verify_totp_code(user.mfa_secret, code):
        return None

    log_event(db, actor=user.id, action="user.login", target=f"user:{user.id}", reason="mfa")
    return user
