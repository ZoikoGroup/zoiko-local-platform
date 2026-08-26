"""Contact-center-lite (architecture doc's "queues" - Phase 3), distinct
from the ring-group/FORWARD call-flow node: a real FIFO hold queue backed
by Twilio's native <Enqueue>/<Leave>/<Dial><Queue> primitives, agent
presence (available/wrap-up/offline), and an agent-pull ("answer next
caller") workflow rather than the server proactively cold-calling agents.
Deliberately no auto-dial-on-available and no skills-based routing - the
"lite" in the name.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.events.service import publish_agent_presence_changed, publish_queue_created
from app.integrations.cache.redis import cache_delete, cache_get, cache_set
from app.integrations.telecom import twilio as telecom
from app.numbering.identity.models import User
from app.queues.models import (
    AgentPresence,
    AgentPresenceStatus,
    CallQueue,
    QueueCallLog,
    QueueCallOutcome,
    QueueMember,
)


class QueueNotFoundError(Exception):
    pass


class UserNotInAccountError(Exception):
    pass


class InvalidPresenceStatusError(Exception):
    pass


class NoWaitingCallerError(Exception):
    pass


class AgentPhoneNotSetError(Exception):
    pass


class NotAQueueMemberError(Exception):
    pass


def twilio_queue_name(queue_id: str) -> str:
    return f"zoiko-queue-{queue_id}"


def _get_queue(db: Session, account_id: str, queue_id: str) -> CallQueue:
    queue = db.query(CallQueue).filter(CallQueue.id == queue_id, CallQueue.account_id == account_id).first()
    if queue is None:
        raise QueueNotFoundError(queue_id)
    return queue


def _to_response(db: Session, queue: CallQueue) -> dict:
    rows = (
        db.query(User.id, User.email)
        .join(QueueMember, QueueMember.user_id == User.id)
        .filter(QueueMember.queue_id == queue.id)
        .all()
    )
    return {
        "id": queue.id,
        "account_id": queue.account_id,
        "name": queue.name,
        "max_wait_seconds": queue.max_wait_seconds,
        "wrap_up_seconds": queue.wrap_up_seconds,
        "created_at": queue.created_at,
        "members": [{"user_id": r[0], "email": r[1]} for r in rows],
    }


def _queues_cache_key(account_id: str) -> str:
    return f"queues:list:{account_id}"


_QUEUES_CACHE_TTL_SECONDS = 30


def _serialize_queue_response(q: dict) -> dict:
    return {**q, "created_at": q["created_at"].isoformat() if q["created_at"] else None}


def _deserialize_queue_response(data: dict) -> dict:
    return {**data, "created_at": datetime.fromisoformat(data["created_at"]) if data["created_at"] else None}


def _invalidate_queues_cache(account_id: str) -> None:
    cache_delete(_queues_cache_key(account_id))


def create_queue(db: Session, account_id: str, name: str, max_wait_seconds: int, wrap_up_seconds: int, actor_id: str) -> dict:
    queue = CallQueue(account_id=account_id, name=name, max_wait_seconds=max_wait_seconds, wrap_up_seconds=wrap_up_seconds)
    db.add(queue)
    db.commit()
    _invalidate_queues_cache(account_id)
    log_event(db, actor_id=account_id, action="queue.created", target_type="call_queue", target_id=queue.id,
               metadata={"name": name})
    publish_queue_created(account_id, queue_id=queue.id, name=name)
    return _to_response(db, queue)


def list_queues(db: Session, account_id: str) -> list[dict]:
    cache_key = _queues_cache_key(account_id)
    cached = cache_get(cache_key)
    if cached is not None:
        return [_deserialize_queue_response(row) for row in cached]
    queues = db.query(CallQueue).filter(CallQueue.account_id == account_id).order_by(CallQueue.created_at.desc()).all()
    responses = [_to_response(db, q) for q in queues]
    cache_set(cache_key, [_serialize_queue_response(r) for r in responses], ttl_seconds=_QUEUES_CACHE_TTL_SECONDS)
    return responses


def get_queue_detail(db: Session, account_id: str, queue_id: str) -> dict:
    return _to_response(db, _get_queue(db, account_id, queue_id))


def update_queue(db: Session, account_id: str, queue_id: str, name: str | None, max_wait_seconds: int | None,
                  wrap_up_seconds: int | None) -> dict:
    queue = _get_queue(db, account_id, queue_id)
    if name is not None:
        queue.name = name
    if max_wait_seconds is not None:
        queue.max_wait_seconds = max_wait_seconds
    if wrap_up_seconds is not None:
        queue.wrap_up_seconds = wrap_up_seconds
    db.commit()
    _invalidate_queues_cache(account_id)
    return _to_response(db, queue)


def add_member(db: Session, account_id: str, queue_id: str, user_id: str) -> dict:
    queue = _get_queue(db, account_id, queue_id)
    member_user = db.query(User).filter(User.id == user_id, User.account_id == account_id).first()
    if member_user is None:
        raise UserNotInAccountError(user_id)
    exists = db.query(QueueMember).filter(QueueMember.queue_id == queue_id, QueueMember.user_id == user_id).first()
    if exists is None:
        db.add(QueueMember(queue_id=queue_id, user_id=user_id))
        db.commit()
        _invalidate_queues_cache(account_id)
    return _to_response(db, queue)


def remove_member(db: Session, account_id: str, queue_id: str, user_id: str) -> dict:
    queue = _get_queue(db, account_id, queue_id)
    db.query(QueueMember).filter(QueueMember.queue_id == queue_id, QueueMember.user_id == user_id).delete()
    db.commit()
    _invalidate_queues_cache(account_id)
    return _to_response(db, queue)


def is_queue_member(db: Session, queue_id: str, user_id: str) -> bool:
    return (
        db.query(QueueMember).filter(QueueMember.queue_id == queue_id, QueueMember.user_id == user_id).first()
        is not None
    )


# --- Agent presence ---


def _get_or_create_presence(db: Session, user_id: str) -> AgentPresence:
    presence = db.query(AgentPresence).filter(AgentPresence.user_id == user_id).first()
    if presence is None:
        presence = AgentPresence(user_id=user_id, status=AgentPresenceStatus.OFFLINE)
        db.add(presence)
        db.commit()
    return presence


def effective_status(presence: AgentPresence) -> AgentPresenceStatus:
    if presence.status == AgentPresenceStatus.WRAP_UP and presence.wrap_up_until is not None:
        if datetime.utcnow() >= presence.wrap_up_until.replace(tzinfo=None):
            return AgentPresenceStatus.AVAILABLE
    return presence.status


def get_presence(db: Session, user_id: str) -> AgentPresence:
    return _get_or_create_presence(db, user_id)


def set_presence(db: Session, user_id: str, status: str) -> AgentPresence:
    if status not in (AgentPresenceStatus.AVAILABLE.value, AgentPresenceStatus.OFFLINE.value):
        # WRAP_UP is system-managed only (see start_wrap_up) - an agent
        # can't set it directly, only end it early by going AVAILABLE/OFFLINE.
        raise InvalidPresenceStatusError(status)
    presence = _get_or_create_presence(db, user_id)
    presence.status = AgentPresenceStatus(status)
    presence.changed_at = datetime.utcnow()
    presence.wrap_up_until = None
    db.commit()
    agent = db.query(User).filter(User.id == user_id).first()
    if agent is not None:
        publish_agent_presence_changed(agent.account_id, user_id=user_id, status=status)
    return presence


def start_wrap_up(db: Session, user_id: str, wrap_up_seconds: int) -> AgentPresence:
    presence = _get_or_create_presence(db, user_id)
    now = datetime.utcnow()
    presence.status = AgentPresenceStatus.WRAP_UP
    presence.changed_at = now
    presence.wrap_up_until = now + timedelta(seconds=wrap_up_seconds)
    db.commit()
    return presence


# --- Queue call lifecycle (driven by Twilio webhooks - see media/voice.py + queues/routes.py) ---


def enter_or_get_log(db: Session, queue_id: str, call_sid: str, caller_number: str, phone_number_e164: str | None) -> QueueCallLog:
    log = db.query(QueueCallLog).filter(QueueCallLog.queue_id == queue_id, QueueCallLog.call_sid == call_sid).first()
    if log is None:
        log = QueueCallLog(
            queue_id=queue_id, call_sid=call_sid, caller_number=caller_number, phone_number_e164=phone_number_e164
        )
        db.add(log)
        db.commit()
    return log


def has_exceeded_max_wait(queue: CallQueue, elapsed_seconds: int) -> bool:
    return elapsed_seconds >= queue.max_wait_seconds


def finalize_log(db: Session, call_sid: str, default_outcome: QueueCallOutcome) -> None:
    logs = db.query(QueueCallLog).filter(QueueCallLog.call_sid == call_sid, QueueCallLog.left_at.is_(None)).all()
    for log in logs:
        log.left_at = datetime.utcnow()
        log.outcome = QueueCallOutcome.ANSWERED if log.answered_at is not None else default_outcome
    db.commit()


def mark_answered(db: Session, queue_id: str, agent_user_id: str, preferred_log_id: str | None = None) -> QueueCallLog | None:
    row = None
    if preferred_log_id:
        row = (
            db.query(QueueCallLog)
            .filter(QueueCallLog.id == preferred_log_id, QueueCallLog.left_at.is_(None), QueueCallLog.answered_at.is_(None))
            .first()
        )
    if row is None:
        row = (
            db.query(QueueCallLog)
            .filter(QueueCallLog.queue_id == queue_id, QueueCallLog.left_at.is_(None), QueueCallLog.answered_at.is_(None))
            .order_by(QueueCallLog.entered_at.asc())
            .first()
        )
    if row is None:
        return None
    row.answered_at = datetime.utcnow()
    row.agent_user_id = agent_user_id
    db.commit()
    return row


def get_queue_status(db: Session, queue: CallQueue) -> dict:
    open_rows = db.query(QueueCallLog).filter(QueueCallLog.queue_id == queue.id, QueueCallLog.left_at.is_(None)).all()
    waiting = [r for r in open_rows if r.answered_at is None]
    in_progress = [r for r in open_rows if r.answered_at is not None]
    now = datetime.utcnow()
    longest_wait = 0
    if waiting:
        longest_wait = max(int((now - r.entered_at.replace(tzinfo=None)).total_seconds()) for r in waiting)
    return {
        "queue_id": queue.id,
        "waiting_count": len(waiting),
        "in_progress_count": len(in_progress),
        "longest_wait_seconds": longest_wait,
        "sla_breached": longest_wait > queue.max_wait_seconds if waiting else False,
    }


def pull_next_caller(db: Session, queue: CallQueue, agent: User, base_url: str) -> dict:
    if not is_queue_member(db, queue.id, agent.id):
        raise NotAQueueMemberError(agent.id)
    if not agent.phone_number:
        raise AgentPhoneNotSetError(agent.id)

    oldest = (
        db.query(QueueCallLog)
        .filter(QueueCallLog.queue_id == queue.id, QueueCallLog.left_at.is_(None), QueueCallLog.answered_at.is_(None))
        .order_by(QueueCallLog.entered_at.asc())
        .first()
    )
    if oldest is None:
        raise NoWaitingCallerError(queue.id)

    agent_connect_url = (
        f"{base_url}media/voice/queue/agent-connect"
        f"?queue_id={queue.id}&agent_user_id={agent.id}&queue_call_log_id={oldest.id}"
    )
    status_callback_url = f"{base_url}media/voice/queue/agent-call-ended?agent_user_id={agent.id}&queue_id={queue.id}"

    # This places a real outbound Twilio call to agent.phone_number (a
    # plain, self-service-editable string - see identity.routes.
    # set_phone_number) - it must clear the exact same kill-switch/billing-
    # suspension/toll-fraud gates as every other real outbound call in this
    # codebase (media.service._dispatch_outbound_call, place_bridge_call).
    # Confirmed live: this previously called telecom.place_call directly
    # with none of those checks at all.
    from app.media.service import CallDirection, assert_outbound_call_allowed, record_call
    from app.numbering.identity.models import UserRole

    from_number = oldest.phone_number_e164 or agent.phone_number
    owner = db.query(User).filter(User.account_id == queue.account_id, User.role == UserRole.OWNER).first()
    assert_outbound_call_allowed(
        db, account_id=queue.account_id, account_email=owner.email if owner else "",
        to=agent.phone_number, from_number=from_number,
    )

    result = telecom.place_call(
        to=agent.phone_number,
        from_=from_number,
        twiml_url=agent_connect_url,
        status_callback_url=status_callback_url,
    )
    # Without a CallRecord, this call is invisible to assert_outbound_
    # velocity_ok's own count on every subsequent pull, and to usage/
    # billing metering - both of which key off CallRecord, not just the
    # audit log below.
    record_call(
        db, account_id=queue.account_id, phone_number_id=None, direction=CallDirection.OUTBOUND,
        from_number=from_number, to_number=agent.phone_number,
        provider_call_sid=result["sid"], status=result["status"],
    )
    log_event(db, actor_id=queue.account_id, action="queue.agent_pulled_caller", target_type="call_queue",
               target_id=queue.id, metadata={"agent_user_id": agent.id, "queue_call_log_id": oldest.id})
    return {"call_sid": result["sid"], "caller_number": oldest.caller_number, "queue_call_log_id": oldest.id}
