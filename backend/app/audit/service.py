import hashlib
import json
import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.events.service import publish_audit_event_recorded

logger = logging.getLogger(__name__)


def _hash_state(state: dict[str, Any] | None) -> str | None:
    if state is None:
        return None
    canonical = json.dumps(state, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


def _resolve_account_id(db: Session, *, actor: str, target: str) -> str | None:
    """Best-effort account attribution, computed once at write time instead
    of re-derived on every read. Mirrors the same three shapes the old
    query-time version of list_account_events used to infer on the fly:
    actor=account_id directly (numbering/media/compliance events), actor=
    user.id (signup/login/MFA/team/porting events), and target=
    "compliance_case:{id}" / "porting_request:{id}" (staff acting on a
    customer's case or porting request). Not guaranteed-complete - staff/
    system/cross-account actions correctly resolve to None.

    actor is free text (e.g. "system", "hubspot_oauth_callback", staff
    ids) - the _is_uuid guards below are load-bearing, not decoration: an
    id column is UUID-typed in Postgres, so comparing it against a non-
    UUID string raises InvalidTextRepresentation and aborts the caller's
    transaction, which previously took down every notification-triggering
    endpoint in the app, including signup itself."""
    from app.compliance.models import ComplianceCase
    from app.numbering.identity.models import Account, User
    from app.porting.models import PortingRequest

    if _is_uuid(actor):
        if db.query(Account.id).filter(Account.id == actor).first() is not None:
            return actor

        user = db.query(User).filter(User.id == actor).first()
        if user is not None:
            return user.account_id

    if target.startswith("compliance_case:"):
        case_id = target.split(":", 1)[1]
        if _is_uuid(case_id):
            case = db.query(ComplianceCase).filter(ComplianceCase.id == case_id).first()
            if case is not None:
                return case.account_id

    if target.startswith("porting_request:"):
        request_id = target.split(":", 1)[1]
        if _is_uuid(request_id):
            request = db.query(PortingRequest).filter(PortingRequest.id == request_id).first()
            if request is not None:
                return request.account_id

    return None


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
    account_id: str | None = None,
    # Compatibility with the actor_id/target_type+target_id/metadata calling
    # convention used by media/intelligence (merged from a parallel branch) -
    # same table, just a different shape at the call site. New code should
    # prefer actor/target directly.
    actor_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent | None:
    """Record an immutable audit entry. Call this for every state-changing
    action — signup, login, number purchase, admin override, etc.

    Never raises: callers commonly perform their real state change first and
    then call this, sometimes with more logic (auto-suspend, notifications)
    after it - a transient audit-write failure (DB blip, etc.) must not take
    down business logic that already happened or that still needs to run.
    A failure here is logged and swallowed; it returns None instead of the
    persisted AuditEvent. Nothing in the codebase reads the return value.
    """
    resolved_actor = actor if actor is not None else actor_id
    resolved_target = target
    if resolved_target is None and target_type is not None and target_id is not None:
        resolved_target = f"{target_type}:{target_id}"
    resolved_after = after if after is not None else metadata

    if resolved_actor is None or resolved_target is None:
        raise ValueError("log_event requires actor+target (or actor_id+target_type+target_id)")

    resolved_account_id = account_id
    if resolved_account_id is None:
        resolved_account_id = _resolve_account_id(db, actor=resolved_actor, target=resolved_target)

    event = AuditEvent(
        actor=resolved_actor,
        action=action,
        target=resolved_target,
        account_id=resolved_account_id,
        reason=reason,
        correlation_id=correlation_id,
        before_hash=_hash_state(before),
        after_hash=_hash_state(resolved_after),
    )
    try:
        db.add(event)
        db.commit()
        db.refresh(event)
    except Exception:
        db.rollback()
        logger.exception("log_event failed to persist audit entry action=%s target=%s", action, resolved_target)
        return None

    publish_audit_event_recorded(resolved_account_id, audit_id=event.id, action=action, target=resolved_target)
    return event


def list_account_events(db: Session, account_id: str, limit: int = 200) -> list[AuditEvent]:
    """Customer-facing subset of the audit trail (Owner/Admin only - see
    require_admin on the route). account_id is now a real indexed column,
    resolved once at write time by log_event/_resolve_account_id - see
    that function's docstring for what it can and can't attribute."""
    return (
        db.query(AuditEvent)
        .filter(AuditEvent.account_id == account_id)
        .order_by(AuditEvent.created_at.desc())
        .limit(limit)
        .all()
    )
