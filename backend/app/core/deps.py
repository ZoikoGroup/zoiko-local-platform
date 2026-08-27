from datetime import datetime

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.numbering.identity.models import User, UserRole
from app.staff.models import PlatformStaff, PlatformStaffRole, StaffCapabilityGrant

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)

# get_current_user/get_current_staff run on nearly every authenticated
# request in the app (require_admin/require_writer/require_capability all
# build on top of one or the other) - caching the identity lookup here is
# the single highest-leverage place to cut request latency, since it's
# multiplied across almost every page a logged-in user or staff member
# loads. Short TTL: a role/email/MFA change takes up to this long to
# reach an already-issued session's cached copy - acceptable staleness
# for a first pass, same "conservative, not tuned" posture as every other
# threshold in this codebase. Confirmed safe to return a plain
# reconstructed (session-detached) object for these two: nothing anywhere
# in this codebase touches a relationship or mutates-then-commits
# current_user/staff directly - every call site only reads plain columns
# (account_id, id, role, email, ...).
_AUTH_CACHE_TTL_SECONDS = 30


def _user_cache_key(user_id: str) -> str:
    return f"auth:user:{user_id}"


def _staff_cache_key(staff_id: str) -> str:
    return f"auth:staff:{staff_id}"


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "account_id": user.account_id,
        "email": user.email,
        "phone_number": user.phone_number,
        "hashed_password": user.hashed_password,
        "role": user.role.value,
        "mfa_secret": user.mfa_secret,
        "mfa_enabled": user.mfa_enabled,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


def _deserialize_user(data: dict) -> User:
    return User(
        id=data["id"],
        account_id=data["account_id"],
        email=data["email"],
        phone_number=data["phone_number"],
        hashed_password=data["hashed_password"],
        role=UserRole(data["role"]),
        mfa_secret=data["mfa_secret"],
        mfa_enabled=data["mfa_enabled"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def invalidate_cached_user(user_id: str) -> None:
    """Called after a mutation whose result current_user callers should
    see right away rather than waiting out the TTL (e.g. MFA toggled) -
    see app.numbering.identity.service's enable_mfa/disable_mfa."""
    cache_delete(_user_cache_key(user_id))


def get_current_user(
    request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> User:
    """A logged-in customer account user (signup/login). Rejects staff
    tokens - a staff login can never be used as if it were a customer."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None or payload.get("scope") != "customer":
        raise credentials_error

    user_id = payload.get("sub")
    cached = cache_get(_user_cache_key(user_id))
    if cached is not None:
        user = _deserialize_user(cached)
    else:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None:
            raise credentials_error
        cache_set(_user_cache_key(user_id), _serialize_user(user), ttl_seconds=_AUTH_CACHE_TTL_SECONDS)

    # For app.core.error_logging.ErrorLoggingMiddleware - lets a 5xx logged
    # to error_events be traced back to the account/user that hit it,
    # without every route needing to know about error logging itself.
    request.state.account_id = user.account_id
    request.state.user_id = user.id

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """A customer account Owner/Admin - scoped to THEIR OWN account only.
    Do not use this for anything that should be reviewed independently
    of the customer (e.g. KYC approval) - use get_current_staff instead."""
    if current_user.role not in (UserRole.OWNER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin or owner role required",
        )
    return current_user


def require_writer(current_user: User = Depends(get_current_user)) -> User:
    """Any account role EXCEPT Viewer - use on every write endpoint that
    isn't already Owner/Admin-only via require_admin. A Viewer has full
    read access account-wide (see UserRole.VIEWER's docstring) but must
    never be able to change anything."""
    if current_user.role == UserRole.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Viewer role is read-only",
        )
    return current_user


def require_paid_or_read_only(request: Request, db: Session = Depends(get_db)) -> None:
    """Applied router-wide (dependencies=[Depends(...)] at include_router,
    not per-route) to every gated feature router in app.main - a TRIALING
    account can still read (GET/HEAD/OPTIONS) every gated section, since
    the dashboard Home page's own stat cards depend on reading numbers/
    calls/voicemail/video/receptionist data even for a brand-new trial
    account with nothing in it yet, but any write action needs a paid
    plan. Deliberately router-wide rather than the usual per-route
    Depends() pattern elsewhere in this file - the point is automatic
    coverage of every route in a gated router (present and future),
    which per-route repetition can't guarantee.

    Deliberately does NOT depend on get_current_user: that pulls in
    oauth2_scheme, which auto-401s on a missing/invalid token before this
    function's body ever runs - so a router-wide dependency on it would
    force customer auth onto every route in a gated router, including
    plain public GETs (e.g. GET /compliance/rules) and staff-authenticated
    or webhook routes that were never meant to need a customer JWT at all
    (found via a real test failure, not by inspection). Instead this does
    its own soft/optional token check: on anything that isn't a valid,
    unexpired *customer*-scope token, it simply steps aside - the route's
    own Depends(get_current_user)/require_admin/require_writer (present on
    every gated write route per this file's normal per-route convention)
    is what actually rejects a missing/invalid/wrong-scope token with 401.
    This gate only ever adds a 402 on top of an already-valid customer
    session that turns out to be TRIALING."""
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return
    payload = decode_access_token(auth_header[7:])
    if payload is None or payload.get("scope") != "customer":
        return
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is None:
        return

    from app.billing.models import SubscriptionStatus
    from app.billing.service import TrialWriteRestrictedError, get_or_create_subscription

    sub = get_or_create_subscription(db, user.account_id)
    # get_or_create_subscription auto-flips a lapsed trial straight to
    # ACTIVE with no payment (no payment processor exists yet - see that
    # function's docstring), but leaves trial_ends_at populated when it
    # does. A deliberate upgrade (change_plan) is the only other path to
    # ACTIVE, and it explicitly clears trial_ends_at to None - so a non-NULL
    # trial_ends_at on an ACTIVE subscription means "still running on the
    # lapsed trial," not "genuinely paid," and must stay gated.
    if sub.status == SubscriptionStatus.TRIALING or (
        sub.status == SubscriptionStatus.ACTIVE and sub.trial_ends_at is not None
    ):
        raise TrialWriteRestrictedError(
            "Upgrade your plan to use this feature - you can view it during your trial, but changes need a paid plan."
        )


def _serialize_staff(staff: PlatformStaff) -> dict:
    return {
        "id": staff.id,
        "email": staff.email,
        "hashed_password": staff.hashed_password,
        "role": staff.role.value,
        "is_active": staff.is_active,
        "created_at": staff.created_at.isoformat() if staff.created_at else None,
    }


def _deserialize_staff(data: dict) -> PlatformStaff:
    return PlatformStaff(
        id=data["id"],
        email=data["email"],
        hashed_password=data["hashed_password"],
        role=PlatformStaffRole(data["role"]),
        is_active=data["is_active"],
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def get_current_staff(request: Request, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    """A logged-in Zoiko platform staff member. Rejects customer tokens -
    no customer, including an account Owner, can act as staff."""
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_access_token(token)
    if payload is None or payload.get("scope") != "staff":
        raise credentials_error

    staff_id = payload.get("sub")
    cached = cache_get(_staff_cache_key(staff_id))
    if cached is not None:
        staff = _deserialize_staff(cached)
    else:
        staff = db.query(PlatformStaff).filter(PlatformStaff.id == staff_id).first()
        if staff is None or not staff.is_active:
            raise credentials_error
        # Only ever caches an ACTIVE staff row - a deactivated account is
        # re-checked against the database on every request, never cached
        # as still-active for the TTL window.
        cache_set(_staff_cache_key(staff_id), _serialize_staff(staff), ttl_seconds=_AUTH_CACHE_TTL_SECONDS)

    # See get_current_user's identical note - staff has no account_id.
    request.state.user_id = staff.id

    return staff


def get_api_key_account_id(
    request: Request, authorization: str | None = Depends(api_key_header), db: Session = Depends(get_db)
) -> str:
    """Auth for the /public/v1 API surface only - a raw API key (see
    app.apikeys.service), never a JWT. Distinct from get_current_user
    because a public API key represents the ACCOUNT, not a specific
    logged-in user - there's no User object to return, just the
    account_id every /public/v1 route scopes its query to."""
    from app.apikeys.service import authenticate_api_key

    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key",
    )
    if not authorization:
        raise credentials_error

    raw_key = authorization.removeprefix("Bearer ").strip()
    key = authenticate_api_key(db, raw_key)
    if key is None:
        raise credentials_error

    request.state.account_id = key.account_id
    return key.account_id


def require_entitlement(key: str):
    """ZL-COM-ENT-001 §23: "RBAC and commercial entitlement are independent
    authorization dimensions... ALLOW requires both." Composes as its own
    Depends() alongside require_admin/require_writer, never fused into one
    guard - same separation this file already keeps between role checks
    and app.staff's data-driven require_capability. Use on any customer
    JWT-authenticated route; see require_entitlement_for_api_key below for
    the /public/v1 (raw API key) equivalent."""

    def _dependency(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        from app.billing.service import get_or_create_subscription, has_entitlement

        if not has_entitlement(db, current_user.account_id, key):
            sub = get_or_create_subscription(db, current_user.account_id)
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": key, "current_plan": sub.plan_code},
            )
        return current_user

    return _dependency


def require_entitlement_for_api_key(key: str):
    """Same as require_entitlement above, but for the /public/v1 surface,
    which authenticates via get_api_key_account_id (a raw API key, no User
    object) rather than a customer JWT."""

    def _dependency(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)) -> str:
        from app.billing.service import get_or_create_subscription, has_entitlement

        if not has_entitlement(db, account_id, key):
            sub = get_or_create_subscription(db, account_id)
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": key, "current_plan": sub.plan_code},
            )
        return account_id

    return _dependency


def require_entitlement_scope(key: str, min_scope: str):
    """Same purpose as require_entitlement, but for the enum-typed 'scope
    ladder' keys ZL-COM-ENT-001 v3.0 introduces (developer.api.scope,
    developer.webhooks.scope: none < limited < standard < advanced <
    contracted) - a plain boolean check can't express "at least limited"."""

    def _dependency(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
        from app.billing.service import get_or_create_subscription, has_entitlement_scope

        if not has_entitlement_scope(db, current_user.account_id, key, min_scope):
            sub = get_or_create_subscription(db, current_user.account_id)
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "ENTITLEMENT_REQUIRED", "entitlement": key,
                    "required_scope": min_scope, "current_plan": sub.plan_code,
                },
            )
        return current_user

    return _dependency


def require_entitlement_scope_for_api_key(key: str, min_scope: str):
    """Same as require_entitlement_scope above, but for the /public/v1
    surface - see require_entitlement_for_api_key."""

    def _dependency(account_id: str = Depends(get_api_key_account_id), db: Session = Depends(get_db)) -> str:
        from app.billing.service import get_or_create_subscription, has_entitlement_scope

        if not has_entitlement_scope(db, account_id, key, min_scope):
            sub = get_or_create_subscription(db, account_id)
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail={
                    "code": "ENTITLEMENT_REQUIRED", "entitlement": key,
                    "required_scope": min_scope, "current_plan": sub.plan_code,
                },
            )
        return account_id

    return _dependency


def require_capability(capability: str):
    """Data-driven segregation of duties (Commercial Billing Operating
    Standard doc's "formal RBAC/segregation-of-duties matrix" ask) - see
    app.staff.models.StaffCapabilityGrant's docstring. Looks up which
    PlatformStaffRoles may perform `capability` from the
    staff_capability_grants table instead of taking an explicit role list
    as a Python argument at each call site, so who-can-do-what is data
    (editable without a deploy) rather than scattered across route files.
    Returns a dependency, not a dependency itself - use as
    Depends(require_capability("billing.simulate_payment_event")).

    Fails closed: a capability with zero configured grants (a seeding gap,
    or a new route nobody granted yet) denies every role rather than
    silently letting all staff through - the opposite failure mode would
    turn a missing seed row into an unintended privilege escalation."""

    def dependency(
        staff: PlatformStaff = Depends(get_current_staff), db: Session = Depends(get_db)
    ) -> PlatformStaff:
        allowed_roles = {
            row[0]
            for row in db.query(StaffCapabilityGrant.role)
            .filter(StaffCapabilityGrant.capability == capability)
            .all()
        }
        if staff.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires the '{capability}' capability",
            )
        return staff

    return dependency
