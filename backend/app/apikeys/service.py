import hashlib
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.apikeys.models import ApiKey
from app.audit.service import log_event
from app.events.service import publish_api_key_created, publish_api_key_revoked
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.notifications.service import notify_api_client_created

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

    _invalidate_api_keys_cache(account_id)
    log_event(db, actor=actor, action="api_key.created", target=f"api_key:{key.id}", after={"label": label})
    publish_api_key_created(account_id, key_id=key.id, label=label)

    from app.numbering.identity.models import User, UserRole

    actor_user = db.query(User).filter(User.id == actor).first()
    owner = db.query(User).filter(User.account_id == account_id, User.role == UserRole.OWNER).first()
    if owner is not None:
        notify_api_client_created(
            db, account_id=account_id, account_email=owner.email, label=label,
            actor_display_name=actor_user.email if actor_user else "your account",
        )

    return key, raw_key


def _api_keys_cache_key(account_id: str) -> str:
    return f"api_keys:list:{account_id}"


# Capped at _MAX_KEYS_PER_ACCOUNT (10) rows and a rarely-visited page, so
# the perf win is small - included anyway to close out the same gap
# consistently across every account-scoped list in the app.
_API_KEYS_CACHE_TTL_SECONDS = 30


def _serialize_api_key(k: ApiKey) -> dict:
    return {
        "id": k.id,
        "account_id": k.account_id,
        "label": k.label,
        "key_prefix": k.key_prefix,
        "key_hash": k.key_hash,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
        "created_at": k.created_at.isoformat() if k.created_at else None,
    }


def _deserialize_api_key(data: dict) -> ApiKey:
    return ApiKey(
        id=data["id"],
        account_id=data["account_id"],
        label=data["label"],
        key_prefix=data["key_prefix"],
        key_hash=data["key_hash"],
        last_used_at=datetime.fromisoformat(data["last_used_at"]) if data["last_used_at"] else None,
        revoked_at=datetime.fromisoformat(data["revoked_at"]) if data["revoked_at"] else None,
        created_at=datetime.fromisoformat(data["created_at"]) if data["created_at"] else None,
    )


def _invalidate_api_keys_cache(account_id: str) -> None:
    cache_delete(_api_keys_cache_key(account_id))


def list_api_keys(db: Session, account_id: str) -> list[ApiKey]:
    cache_key = _api_keys_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_api_key(row) for row in cached]
    keys = db.query(ApiKey).filter(ApiKey.account_id == account_id).order_by(ApiKey.created_at.desc()).all()
    cache_set(cache_key, [_serialize_api_key(k) for k in keys], ttl_seconds=_API_KEYS_CACHE_TTL_SECONDS)
    return keys


def revoke_api_key(db: Session, *, account_id: str, key_id: str, actor: str) -> None:
    key = db.query(ApiKey).filter(ApiKey.id == key_id).first()
    if key is None or key.account_id != account_id:
        raise ApiKeyAuthorizationError(f"{key_id} is not an API key on your account")

    key.revoked_at = datetime.now(timezone.utc)
    db.commit()
    _invalidate_api_keys_cache(account_id)
    log_event(db, actor=actor, action="api_key.revoked", target=f"api_key:{key_id}")
    publish_api_key_revoked(account_id, key_id=key_id)


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
