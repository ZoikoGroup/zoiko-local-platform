import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent


def _hash_state(state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    canonical = json.dumps(state, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def log_event(
    db: Session,
    *,
    actor: str,
    action: str,
    target: str,
    reason: str | None = None,
    correlation_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    """Record an immutable audit entry. Call this for every state-changing
    action — signup, login, number purchase, admin override, etc.
    """
    event = AuditEvent(
        actor=actor,
        action=action,
        target=target,
        reason=reason,
        correlation_id=correlation_id,
        before_hash=_hash_state(before),
        after_hash=_hash_state(after),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
