from fastapi import Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader, OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.numbering.identity.models import User, UserRole
from app.staff.models import PlatformStaff, StaffCapabilityGrant

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


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

    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is None:
        raise credentials_error

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

    staff = db.query(PlatformStaff).filter(PlatformStaff.id == payload.get("sub")).first()
    if staff is None or not staff.is_active:
        raise credentials_error

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
