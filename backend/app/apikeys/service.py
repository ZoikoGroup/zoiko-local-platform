import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.apikeys.models import ApiKey
from app.audit.service import log_event

_MAX_KEYS_PER_ACCOUNT = 10
_KEY_PREFIX = "zlk_live_"


class ApiKeyAuthorizationError(Exception):
    """Raised when the caller's account doesn't own the given key."""


class ApiKeyLimitExceededError(Exception):
    """Raised when an account already has the max number of active keys."""


def _hash(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


def create_api_key(db: Session, *, account_id: str, label: str, actor: str) -> tuple[ApiKey, str]:
    """Returns (key_row, raw_key) - the raw key is generated here and
    returned exactly once (see routes.py); only its SHA-256 hash is
    ever persisted, so a database read alone can never recover it."""
    active_count = (
        db.query(ApiKey).filter(ApiKey.account_id == account_id, ApiKey.revoked_at.is_(None)).count()
    )
    if active_count >= _MAX_KEYS_PER_ACCOUNT:
        raise ApiKeyLimitExceededError(f"Accounts may have up to {_MAX_KEYS_PER_ACCOUNT} active API keys")

    raw_key = _KEY_PREFIX + secrets.token_hex(24)
    key = ApiKey(account_id=account_id, label=label, key_prefix=raw_key[:16], key_hash=_hash(raw_key))
    db.add(key)
    db.commit()
    db.refresh(key)

    log_event(db, actor=actor, action="api_key.created", target=f"api_key:{key.id}", after={"label": label})
    return key, raw_key


def list_api_keys(db: Session, account_id: str) -> list[ApiKey]:
    return db.query(ApiKey).filter(ApiKey.account_id == account_id).order_by(ApiKey.created_at.desc()).all()


def revoke_api_key(db: Session, *, account_id: str, key_id: str, actor: str) -> None:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if key is None or key.account_id != account_id:
        raise ApiKeyAuthorizationError(f"{key_id} is not an API key on your account")

    key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    log_event(db, actor=actor, action="api_key.revoked", target=f"api_key:{key_id}")


def authenticate_api_key(db: Session, raw_key: str) -> ApiKey | None:
    """Looks up an active, non-revoked key by its hash and stamps
    last_used_at - best-effort observability for the developer portal,
    not a rate-limit mechanism (no rate limiting is applied to public API
    keys yet)."""
    key = db.query(ApiKey).filter(ApiKey.key_hash == _hash(raw_key), ApiKey.revoked_at.is_(None)).first()
    if key is None:
        return None
    key.last_used_at = datetime.now(timezone.utc)
    db.commit()
    return key
