from sqlalchemy.orm import Session

from app.audit.models import AuditEvent


def log_event(
    db: Session,
    *,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str,
    metadata: dict | None = None,
) -> AuditEvent:
    """Appends an immutable audit record. Per CLAUDE.md: every state-changing
    action must call this — never skip it to save time.
    """
    event = AuditEvent(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        event_metadata=metadata,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event
