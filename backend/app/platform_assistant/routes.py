from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.numbering.identity.models import User
from app.platform_assistant import service
from app.platform_assistant.schemas import (
    AssistantMessageResponse,
    BootstrapResponse,
    CreateSessionRequest,
    FeedbackRequest,
    MessageRequest,
    SessionCreatedResponse,
    SessionEndResponse,
    SessionResponse,
)

router = APIRouter(prefix="/api/v1/platform-assistant", tags=["platform-assistant"])


def _guard_enabled() -> None:
    if not settings.platform_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The platform assistant is temporarily unavailable.",
        )


def _session_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, service.ContextChangedError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assistant session ended because workspace context changed.",
        )
    if isinstance(exc, service.SessionExpiredError):
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired - create a new session.")
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session not found or already ended.")


@router.get("/bootstrap", response_model=BootstrapResponse)
def bootstrap(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.get_bootstrap(db, current_user)


@router.post("/sessions", response_model=SessionCreatedResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: CreateSessionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _guard_enabled()
    try:
        return service.create_session(db, current_user, module=payload.module, context_key=payload.context_key)
    except service.InvalidModuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported assistant module.") from exc


@router.get("/sessions/{session_id}", response_model=SessionResponse)
def get_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    x_platform_assistant_session: str = Header(alias="X-Platform-Assistant-Session"),
    x_platform_assistant_token: str = Header(alias="X-Platform-Assistant-Token"),
):
    _guard_enabled()
    try:
        return service.get_session(
            db,
            current_user,
            session_id=session_id,
            session_token=x_platform_assistant_token,
        )
    except (service.SessionNotFoundError, service.SessionEndedError, service.SessionExpiredError, service.ContextChangedError) as exc:
        raise _session_errors(exc) from exc


@router.post("/messages", response_model=AssistantMessageResponse)
def send_message(
    payload: MessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    x_platform_assistant_session: str = Header(alias="X-Platform-Assistant-Session"),
    x_platform_assistant_token: str = Header(alias="X-Platform-Assistant-Token"),
):
    _guard_enabled()
    try:
        assistant_session = service.authenticate_session(
            db,
            current_user,
            session_id=x_platform_assistant_session,
            session_token=x_platform_assistant_token,
        )
        return service.handle_message(
            db,
            current_user,
            assistant_session,
            text=payload.text,
            client_message_id=payload.client_message_id,
            intent=payload.intent,
        )
    except (service.SessionNotFoundError, service.SessionEndedError, service.SessionExpiredError, service.ContextChangedError) as exc:
        raise _session_errors(exc) from exc


@router.post("/sessions/end", response_model=SessionEndResponse)
def end_session(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    x_platform_assistant_session: str = Header(alias="X-Platform-Assistant-Session"),
    x_platform_assistant_token: str = Header(alias="X-Platform-Assistant-Token"),
):
    _guard_enabled()
    try:
        return service.end_session(
            db,
            current_user,
            session_id=x_platform_assistant_session,
            session_token=x_platform_assistant_token,
        )
    except (service.SessionNotFoundError, service.SessionEndedError, service.SessionExpiredError):
        return {"ended": True, "session_id": x_platform_assistant_session}


@router.post("/feedback")
def feedback(
    payload: FeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    x_platform_assistant_session: str = Header(alias="X-Platform-Assistant-Session"),
    x_platform_assistant_token: str = Header(alias="X-Platform-Assistant-Token"),
):
    _guard_enabled()
    try:
        assistant_session = service.authenticate_session(
            db,
            current_user,
            session_id=x_platform_assistant_session,
            session_token=x_platform_assistant_token,
        )
        return service.record_feedback(
            db,
            current_user,
            session=assistant_session,
            message_id=payload.message_id,
            rating=payload.rating,
            category=payload.category,
        )
    except (service.SessionNotFoundError, service.SessionEndedError, service.SessionExpiredError, service.ContextChangedError) as exc:
        raise _session_errors(exc) from exc
