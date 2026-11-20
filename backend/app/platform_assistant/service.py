import hashlib
import hmac
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.billing import service as billing_service
from app.core.config import settings
from app.core.ids import new_uuid
from app.numbering.identity.models import User
from app.platform_assistant import content, policies, tools
from app.platform_assistant.models import PlatformAssistantContent, PlatformAssistantEvent, PlatformAssistantSession
from app.platform_assistant.schemas import AssistantMessageResponse, BootstrapResponse, ModuleInfo, SessionContext


SESSION_TTL_MINUTES = 30
_ACTOR_PREFIX = "platform_assistant:"


class SessionNotFoundError(Exception):
    pass


class SessionEndedError(Exception):
    pass


class SessionExpiredError(Exception):
    pass


class ContextChangedError(Exception):
    pass


class InvalidModuleError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _actor(user: User) -> str:
    return f"{_ACTOR_PREFIX}{user.id}"


def _content_map(db: Session) -> dict[str, str]:
    rows = db.query(PlatformAssistantContent).filter(PlatformAssistantContent.is_active.is_(True)).all()
    return {row.content_key: row.body for row in rows}


def ensure_content(db: Session) -> None:
    existing = {row.content_key for row in db.query(PlatformAssistantContent.content_key).all()}
    missing = [row for row in content.CONTENT_ROWS if row["key"] not in existing]
    if not missing:
        return
    for row in missing:
        db.add(
            PlatformAssistantContent(
                content_key=row["key"],
                title=row["title"],
                category=row["category"],
                body=row["body"],
                content_version=content.CONTENT_VERSION,
                is_active=True,
            )
        )
    db.commit()


def _context_fingerprint(db: Session, user: User, module: str, context_key: str | None) -> str:
    snapshot = billing_service.get_entitlement_snapshot(db, user.account_id)
    canonical = json.dumps(
        {
            "account_id": user.account_id,
            "role": user.role.value,
            "module": module,
            "context_key": context_key,
            "entitlements": snapshot,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _session_context_with_role(session: PlatformAssistantSession, role: str) -> SessionContext:
    return SessionContext(
        module=session.module,
        role=role,
        context_key=session.context_key,
        expires_at=session.expires_at.isoformat(),
    )


def get_bootstrap(db: Session, user: User) -> BootstrapResponse:
    ensure_content(db)
    return BootstrapResponse(
        enabled=settings.platform_assistant_enabled,
        assistant_name=content.ASSISTANT_NAME,
        ai_disclosure=content.AI_DISCLOSURE,
        read_only_notice=content.READ_ONLY_NOTICE,
        content_version=content.CONTENT_VERSION,
        supported_modules=[
            ModuleInfo(
                key=key,
                title=value["title"],
                description=value["description"],
                prompts=list(value["prompts"]),
                destination_key=value["destination_key"],
            )
            for key, value in content.MODULES.items()
        ],
    )


def create_session(db: Session, user: User, *, module: str = "overview", context_key: str | None = None) -> dict:
    if module not in content.MODULES:
        raise InvalidModuleError()
    ensure_content(db)
    token = secrets.token_urlsafe(32)
    expires_at = _now() + timedelta(minutes=SESSION_TTL_MINUTES)
    row = PlatformAssistantSession(
        user_id=user.id,
        account_id=user.account_id,
        session_token_hash=_hash_token(token),
        module=module,
        context_key=context_key,
        context_fingerprint=_context_fingerprint(db, user, module, context_key),
        expires_at=expires_at,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_event(
        db,
        actor=_actor(user),
        action="platform_assistant.session_created",
        target=f"platform_assistant_session:{row.id}",
        account_id=user.account_id,
        after={"module": module, "expires_at": expires_at.isoformat()},
    )
    return {
        "session_id": row.id,
        "session_token": token,
        "expires_at": row.expires_at.isoformat(),
        "context": _session_context_with_role(row, user.role.value),
    }


def authenticate_session(db: Session, user: User, *, session_id: str, session_token: str) -> PlatformAssistantSession:
    row = db.query(PlatformAssistantSession).filter(PlatformAssistantSession.id == session_id).first()
    if (
        row is None
        or row.user_id != user.id
        or row.account_id != user.account_id
        or not hmac.compare_digest(row.session_token_hash, _hash_token(session_token))
    ):
        raise SessionNotFoundError()
    if not row.is_active or row.ended_at is not None:
        raise SessionEndedError()
    if row.expires_at <= _now():
        raise SessionExpiredError()
    return row


def get_session(db: Session, user: User, *, session_id: str, session_token: str) -> dict:
    row = authenticate_session(db, user, session_id=session_id, session_token=session_token)
    return {
        "session_id": row.id,
        "context": _session_context_with_role(row, user.role.value),
        "message_count": row.message_count,
        "ended": False,
    }


def end_session(db: Session, user: User, *, session_id: str, session_token: str) -> dict:
    row = authenticate_session(db, user, session_id=session_id, session_token=session_token)
    row.is_active = False
    row.ended_at = _now()
    row.invalidation_reason = "user_ended"
    db.commit()
    log_event(
        db,
        actor=_actor(user),
        action="platform_assistant.session_ended",
        target=f"platform_assistant_session:{row.id}",
        account_id=user.account_id,
        after={"message_count": row.message_count},
    )
    return {"ended": True, "session_id": row.id}


def end_all_for_user(db: Session, user: User) -> None:
    rows = (
        db.query(PlatformAssistantSession)
        .filter(
            PlatformAssistantSession.user_id == user.id,
            PlatformAssistantSession.is_active.is_(True),
        )
        .all()
    )
    now = _now()
    for row in rows:
        row.is_active = False
        row.ended_at = now
        row.invalidation_reason = "logout"
    if rows:
        db.commit()


def record_feedback(db: Session, user: User, *, session: PlatformAssistantSession, message_id: str, rating: str, category: str | None) -> dict:
    _record_event(
        db,
        user,
        session=session,
        event_type="feedback",
        status=rating,
        request_id=message_id,
        metadata={"category": category} if category else {},
    )
    return {"recorded": True}


def handle_message(
    db: Session,
    user: User,
    session: PlatformAssistantSession,
    *,
    text: str,
    client_message_id: str | None = None,
    intent: str | None = None,
) -> AssistantMessageResponse:
    ensure_content(db)
    current_fingerprint = _context_fingerprint(db, user, session.module, session.context_key)
    if current_fingerprint != session.context_fingerprint:
        session.is_active = False
        session.ended_at = _now()
        session.invalidation_reason = "context_changed"
        db.commit()
        raise ContextChangedError()

    request_id = str(uuid.uuid4())
    classification = policies.classify_request(text)
    tool_key = classification["topic"] if classification["topic"] in content.MODULES else session.module
    destination_key = policies.destination_key_for_topic(classification["topic"], session.module)
    copy = _content_map(db)

    if classification["sensitive_detected"]:
        result = tools.ToolResult(
            answer_type="sensitive_blocked",
            content_blocks=[_paragraph(copy.get("unknown_fallback", content.UNKNOWN_FALLBACK))],
            evidence=[],
            actions=[_action(destination_key)],
            uncertainty="high",
            risk_tier=classification["risk_tier"],
            status="blocked",
        )
    elif classification["mutation_detected"]:
        result = tools.ToolResult(
            answer_type="mutation_blocked",
            content_blocks=[_paragraph(copy.get("mutation_fallback", content.MUTATION_FALLBACK))],
            evidence=[],
            actions=[_action(destination_key)],
            uncertainty="low",
            risk_tier=classification["risk_tier"],
            status="blocked",
        )
    elif policies.is_greeting(text):
        result = tools.ToolResult(
            answer_type="answer",
            content_blocks=[_paragraph(content.GREETING_RESPONSE)],
            evidence=[],
            actions=[_action(destination_key)],
            uncertainty="low",
            risk_tier="R0",
            status="ok",
        )
    elif (faq := policies.match_general_faq(text)) is not None:
        result = tools.ToolResult(
            answer_type="answer",
            content_blocks=[_paragraph(faq["answer"])],
            evidence=[_evidence(source_id) for source_id in faq["evidence"]],
            actions=[_action(key) for key in faq["actions"]],
            uncertainty="low",
            risk_tier="R0",
            status="ok",
        )
    else:
        result = tools.run_tool(tool_key, db, user)
        if not result.actions:
            result.actions = [_action(destination_key)]

    session.message_count += 1
    session.last_seen_at = _now()
    db.commit()

    _record_event(
        db,
        user,
        session=session,
        event_type="message",
        status=result.status,
        destination_key=destination_key,
        request_id=request_id,
        risk_tier=result.risk_tier,
        metadata={
            "answer_type": result.answer_type,
            "topic": classification["topic"],
            "tool_key": tool_key,
            "client_message_id_present": client_message_id is not None,
            "intent_present": intent is not None,
        },
    )
    log_event(
        db,
        actor=_actor(user),
        action="platform_assistant.message_sent",
        target=f"platform_assistant_session:{session.id}",
        account_id=user.account_id,
        correlation_id=request_id,
        after={"answer_type": result.answer_type, "risk_tier": result.risk_tier, "module": session.module},
    )

    return AssistantMessageResponse(
        message_id=str(uuid.uuid4()),
        client_message_id=client_message_id,
        request_id=request_id,
        answer_type=result.answer_type,
        context=_session_context_with_role(session, user.role.value),
        content_blocks=[block if "items" in block else {**block, "items": []} for block in result.content_blocks],
        evidence=[item if "masked" in item else {**item, "masked": True} for item in result.evidence],
        available_actions=result.actions,
        uncertainty=result.uncertainty,
        risk_tier=result.risk_tier,
        content_version=content.CONTENT_VERSION,
    )


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "text": text, "items": []}


def _evidence(source_id: str) -> dict:
    return {
        "source_id": source_id,
        "title": content.EVIDENCE_SOURCES.get(source_id, source_id),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "masked": True,
    }


def _action(destination_key: str) -> dict:
    key = destination_key if destination_key in content.DESTINATIONS else "DASHBOARD_HOME"
    destination = content.DESTINATIONS[key]
    return {
        "kind": "navigate",
        "destination_key": key,
        "label": destination["label"],
        "href": destination["href"],
        "read_only": True,
    }


def _record_event(
    db: Session,
    user: User,
    *,
    session: PlatformAssistantSession,
    event_type: str,
    status: str,
    destination_key: str | None = None,
    request_id: str | None = None,
    risk_tier: str = "R0",
    metadata: dict | None = None,
) -> None:
    db.add(
        PlatformAssistantEvent(
            session_id=session.id,
            user_id=user.id,
            account_id=user.account_id,
            event_type=event_type,
            module=session.module,
            risk_tier=risk_tier,
            status=status,
            destination_key=destination_key,
            request_id=request_id,
            metadata_json=metadata or {},
        )
    )
    db.commit()
