from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.rate_limit import limiter
from app.core.security import create_access_token, decode_access_token, verify_google_id_token
from app.numbering.identity import service
from app.numbering.identity.models import User
from app.numbering.identity.schemas import (
    ForgotPasswordRequest,
    GoogleAuthRequest,
    LoginRequest,
    LoginResponse,
    MfaCodeRequest,
    MfaLoginRequest,
    MfaSetupResponse,
    ResetPasswordRequest,
    SetPhoneNumberRequest,
    SignupRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["identity"])
MFA_TOKEN_EXPIRE_MINUTES = 5


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(
    payload: SignupRequest,
    db: Session = Depends(get_db),
    x_device_fingerprint: str | None = Header(default=None),
):
    try:
        user = service.create_account_with_owner(
            db, payload.account_name, payload.account_type, payload.email, payload.password,
            device_fingerprint_hash=x_device_fingerprint,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return user


@router.post("/login", response_model=LoginResponse)
@limiter.limit("5/minute")
def login(request: Request, payload: LoginRequest, db: Session = Depends(get_db)):
    user = service.authenticate_user(db, payload.email, payload.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if user.mfa_enabled:
        mfa_token = create_access_token(
            subject=user.id, scope="mfa_pending", expire_minutes=MFA_TOKEN_EXPIRE_MINUTES
        )
        return LoginResponse(mfa_required=True, mfa_token=mfa_token)

    token = create_access_token(subject=user.id, scope="customer")
    return LoginResponse(access_token=token)


@router.post("/forgot-password", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("5/minute")
def forgot_password(request: Request, payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Always 204, whether or not the email matches an account - never
    reveals which, to avoid account enumeration (see
    service.request_password_reset's docstring)."""
    service.request_password_reset(db, payload.email)
    return None


@router.post("/reset-password", response_model=LoginResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    try:
        user = service.reset_password(db, payload.token, payload.new_password)
    except service.InvalidResetTokenError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e

    # A reset link proves control of the inbox, not the second factor - MFA
    # must still run if it's enabled, same as a normal login, otherwise
    # compromising the email account alone would be enough to fully take
    # over an MFA-protected account.
    if user.mfa_enabled:
        mfa_token = create_access_token(
            subject=user.id, scope="mfa_pending", expire_minutes=MFA_TOKEN_EXPIRE_MINUTES
        )
        return LoginResponse(mfa_required=True, mfa_token=mfa_token)

    token = create_access_token(subject=user.id, scope="customer")
    return LoginResponse(access_token=token)


@router.post("/mfa/login", response_model=TokenResponse)
@limiter.limit("5/minute")
def mfa_login(request: Request, payload: MfaLoginRequest, db: Session = Depends(get_db)):
    claims = decode_access_token(payload.mfa_token)
    if claims is None or claims.get("scope") != "mfa_pending":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired MFA session")

    user = service.complete_mfa_login(db, claims["sub"], payload.code)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid code")

    token = create_access_token(subject=user.id, scope="customer")
    return TokenResponse(access_token=token)


@router.post("/mfa/setup", response_model=MfaSetupResponse)
def mfa_setup(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    secret, uri = service.start_mfa_setup(db, current_user)
    return MfaSetupResponse(secret=secret, otpauth_uri=uri)


@router.post("/mfa/enable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_enable(
    payload: MfaCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        service.enable_mfa(db, current_user, payload.code, actor=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_disable(
    payload: MfaCodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        service.disable_mfa(db, current_user, payload.code, actor=current_user.id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.post("/google", response_model=TokenResponse)
def google_auth(payload: GoogleAuthRequest, db: Session = Depends(get_db)):
    claims = verify_google_id_token(payload.credential)
    if claims is None or not claims.get("email"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Google credential")

    user = service.find_or_create_user_from_google(db, claims["email"], claims.get("name"))
    token = create_access_token(subject=user.id, scope="customer")
    return TokenResponse(access_token=token)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me/phone", response_model=UserResponse)
def set_phone_number(
    payload: SetPhoneNumberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.set_phone_number(db, current_user, payload.phone_number)
