import hashlib
from datetime import datetime, timedelta, timezone

import pyotp
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(subject: str, scope: str = "customer", expire_minutes: int | None = None) -> str:
    """scope distinguishes a customer account token from a platform staff
    token (or a short-lived mfa_pending token), so one can never be used
    where another is required."""
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expire_minutes if expire_minutes is not None else ACCESS_TOKEN_EXPIRE_MINUTES
    )
    payload = {"sub": subject, "scope": scope, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None


def password_fingerprint(hashed_password: str) -> str:
    """A short, non-reversible fingerprint of a user's CURRENT hashed
    password, embedded in a password-reset token's payload. There's no
    reset-token table to mark tokens used - instead, the fingerprint stops
    matching the instant the password actually changes (successfully using
    a reset token changes hashed_password), so any other outstanding token
    for the same request (e.g. a reset email opened twice, or a stale
    link after the password was already changed some other way) is
    automatically invalidated without needing separate revocation state."""
    return hashlib.sha256(hashed_password.encode("utf-8")).hexdigest()[:16]


def verify_google_id_token(credential: str) -> dict | None:
    """Verifies a Google Identity Services credential (ID token) against
    Google's public keys and our own Client ID. Returns the decoded
    payload (contains 'email', 'name', etc.) or None if invalid."""
    try:
        return google_id_token.verify_oauth2_token(
            credential, google_requests.Request(), settings.google_client_id
        )
    except ValueError:
        return None


def generate_mfa_secret() -> str:
    return pyotp.random_base32()


def mfa_provisioning_uri(secret: str, email: str) -> str:
    """The otpauth:// URI an authenticator app (Google Authenticator,
    Authy, etc.) scans as a QR code to start generating codes."""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="Zoiko Local")


def verify_totp_code(secret: str, code: str) -> bool:
    return pyotp.totp.TOTP(secret).verify(code, valid_window=1)
