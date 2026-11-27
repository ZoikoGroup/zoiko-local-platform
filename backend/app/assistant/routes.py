"""Public assistant API (WF-01's proposed surface, following this
codebase's careers routing convention instead of the docs' illustrative
/v1/public-local paths, which the docs themselves mark "PROPOSED - NOT
DEPLOYED"). Genuinely public like careers' public_router: no auth, no
trial gate, no account - the marketing site (zoiko-local-react) calls it
from an anonymous visitor's browser.

Session credentials travel as explicit request headers
(X-Assistant-Session / X-Assistant-Token) rather than cookies - with no
ambient browser authority there is no CSRF surface to defend, while the
per-session secret still prevents one visitor from injecting messages
into another's session. Rate limits mirror the other public surfaces
(careers: 60/min reads); message sending is tighter (30/min) since it
touches the DB, and session creation much tighter (10/min) since a
session is the only state this module ever creates.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.assistant import service
from app.assistant.schemas import (
    AssistantMessageResponse,
    BootstrapResponse,
    CreateSessionRequest,
    MessageRequest,
    SessionCreatedResponse,
    SessionEndResponse,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter

public_router = APIRouter(prefix="/api/v1/assistant", tags=["public-assistant"])


def _guard_enabled() -> None:
    """WF-01 kill switch - one settings flag disables the whole surface
    (bootstrap reports enabled=false so the widget never opens; the
    stateful endpoints refuse outright)."""
    if not settings.public_assistant_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The assistant is temporarily unavailable.",
        )


@public_router.get("/bootstrap", response_model=BootstrapResponse)
@limiter.limit("60/minute")
def bootstrap(request: Request, db: Session = Depends(get_db)):
    # The kill switch deliberately reports enabled=false rather than 503
    # here - the widget's contract is "never offer to open a chat" when
    # disabled, which needs a successful response carrying the flag.
    # Overriding the key keeps the flag outside the service's Redis
    # cache so a flip takes effect immediately, cache or no cache.
    payload = service.get_bootstrap(db)
    payload["enabled"] = settings.public_assistant_enabled
    return payload


@public_router.post("/sessions", response_model=SessionCreatedResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/minute")
def create_session(request: Request, payload: CreateSessionRequest, db: Session = Depends(get_db)):
    _guard_enabled()
    return service.create_session(db, page_context=payload.page_context, locale=payload.locale)


@public_router.post("/messages", response_model=AssistantMessageResponse)
@limiter.limit("30/minute")
def send_message(
    request: Request,
    payload: MessageRequest,
    db: Session = Depends(get_db),
    x_assistant_session: str = Header(alias="X-Assistant-Session"),
    x_assistant_token: str = Header(alias="X-Assistant-Token"),
):
    _guard_enabled()
    try:
        session = service.authenticate_session(
            db, session_id=x_assistant_session, session_token=x_assistant_token
        )
    except (service.SessionNotFoundError, service.SessionEndedError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session not found or already ended - create a new session.",
        )
    except service.SessionExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired - create a new session.",
        )
    selection = payload.selection.model_dump() if payload.selection else None
    return service.handle_message(
        db,
        session,
        intent_id=payload.intent_id,
        text=payload.text,
        selection=selection,
        client_message_id=payload.client_message_id,
    )


@public_router.post("/sessions/end", response_model=SessionEndResponse)
@limiter.limit("10/minute")
def end_session(
    request: Request,
    db: Session = Depends(get_db),
    x_assistant_session: str = Header(alias="X-Assistant-Session"),
    x_assistant_token: str = Header(alias="X-Assistant-Token"),
):
    _guard_enabled()
    try:
        return service.end_session(
            db, session_id=x_assistant_session, session_token=x_assistant_token
        )
    except (service.SessionNotFoundError, service.SessionEndedError, service.SessionExpiredError):
        # Idempotent from the client's point of view: any stale or already
        # ended session simply reports ended, no error to handle.
        return {"ended": True, "session_id": x_assistant_session}
