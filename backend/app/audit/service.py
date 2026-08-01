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
    actor: str | None = None,
    action: str,
    target: str | None = None,
    reason: str | None = None,
    correlation_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    # Compatibility with the actor_id/target_type+target_id/metadata calling
    # convention used by media/intelligence (merged from a parallel branch) -
    # same table, just a different shape at the call site. New code should
    # prefer actor/target directly.
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Record an immutable audit entry. Call this for every state-changing
    action — signup, login, number purchase, admin override, etc.
    """
    resolved_actor = actor if actor is not None else actor_id
    resolved_target = target
    if resolved_target is None and target_type is not None and target_id is not None:
        resolved_target = f"{target_type}:{target_id}"
    resolved_after = after if after is not None else metadata

    if resolved_actor is None or resolved_target is None:
        raise ValueError("log_event requires actor+target (or actor_id+target_type+target_id)")

    event = AuditEvent(
        actor=resolved_actor,
        action=action,
        target=resolved_target,
        reason=reason,
        correlation_id=correlation_id,
        before_hash=_hash_state(before),
        after_hash=_hash_state(resolved_after),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
