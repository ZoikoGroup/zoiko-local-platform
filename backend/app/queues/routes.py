from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.integrations.telecom import twilio as telecom
from app.integrations.telecom.twilio import TelecomError
from app.media import service as media_service
from app.numbering.identity.models import User
from app.queues import service
from app.queues.models import CallQueue, QueueCallOutcome
from app.queues.schemas import (
    AddMemberRequest,
    CreateQueueRequest,
    PresenceResponse,
    PullNextResult,
    QueueResponse,
    QueueStatusResponse,
    SetPresenceRequest,
    UpdateQueueRequest,
)
from app.queues.service import (
    AgentPhoneNotSetError,
    InvalidPresenceStatusError,
    NoWaitingCallerError,
    NotAQueueMemberError,
    QueueNotFoundError,
    UserNotInAccountError,
)

router = APIRouter(prefix="/queues", tags=["queues"])


def _presence_response(presence) -> dict:
    return {
        "status": presence.status.value,
        "changed_at": presence.changed_at,
        "wrap_up_until": presence.wrap_up_until,
        "effectively_available": service.effective_status(presence) == service.AgentPresenceStatus.AVAILABLE,
    }


@router.post("", response_model=QueueResponse, status_code=status.HTTP_201_CREATED)
def create_queue(payload: CreateQueueRequest, db: Session = Depends(get_db), current_user: User = Depends(require_writer)):
    return service.create_queue(
        db, current_user.account_id, payload.name, payload.max_wait_seconds, payload.wrap_up_seconds, current_user.id
    )


@router.get("", response_model=list[QueueResponse])
def list_queues(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return service.list_queues(db, current_user.account_id)


@router.get("/{queue_id}", response_model=QueueResponse)
def get_queue(queue_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        return service.get_queue_detail(db, current_user.account_id, queue_id)
    except QueueNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.put("/{queue_id}", response_model=QueueResponse)
def update_queue(
    queue_id: str, payload: UpdateQueueRequest, db: Session = Depends(get_db), current_user: User = Depends(require_writer)
):
    try:
        return service.update_queue(
            db, current_user.account_id, queue_id, payload.name, payload.max_wait_seconds, payload.wrap_up_seconds
        )
    except QueueNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{queue_id}/members", response_model=QueueResponse)
def add_member(
    queue_id: str, payload: AddMemberRequest, db: Session = Depends(get_db), current_user: User = Depends(require_writer)
):
    try:
        return service.add_member(db, current_user.account_id, queue_id, payload.user_id)
    except QueueNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except UserNotInAccountError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"user {e} not found on this account") from e


@router.delete("/{queue_id}/members/{user_id}", response_model=QueueResponse)
def remove_member(
    queue_id: str, user_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_writer)
):
    try:
        return service.remove_member(db, current_user.account_id, queue_id, user_id)
    except QueueNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.get("/{queue_id}/status", response_model=QueueStatusResponse)
def get_queue_status(queue_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        queue = db.query(CallQueue).filter(CallQueue.id == queue_id, CallQueue.account_id == current_user.account_id).first()
        if queue is None:
            raise QueueNotFoundError(queue_id)
        return service.get_queue_status(db, queue)
    except QueueNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


@router.post("/{queue_id}/pull-next", response_model=PullNextResult)
def pull_next(queue_id: str, request: Request, db: Session = Depends(get_db), current_user: User = Depends(require_writer)):
    try:
        queue = db.query(CallQueue).filter(
            CallQueue.id == queue_id, CallQueue.account_id == current_user.account_id
        ).first()
        if queue is None:
            raise QueueNotFoundError(queue_id)
        return service.pull_next_caller(db, queue, current_user, str(request.base_url))
    except QueueNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NotAQueueMemberError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not a member of this queue") from e
    except AgentPhoneNotSetError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Set your phone number before pulling calls") from e
    except NoWaitingCallerError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="No caller is currently waiting") from e
    except TelecomError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.get("/presence/me", response_model=PresenceResponse)
def get_my_presence(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _presence_response(service.get_presence(db, current_user.id))


@router.put("/presence/me", response_model=PresenceResponse)
def set_my_presence(payload: SetPresenceRequest, db: Session = Depends(get_db), current_user: User = Depends(require_writer)):
    try:
        return _presence_response(service.set_presence(db, current_user.id, payload.status))
    except InvalidPresenceStatusError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"'{e}' is not a settable status - use 'available' or 'offline'",
        ) from e


# --- Twilio webhooks (Advanced IVR builder's QUEUE node lands calls here) ---

webhook_router = APIRouter(prefix="/media/voice/queue", tags=["queues"])


@webhook_router.post("/wait")
async def queue_wait(request: Request, db: Session = Depends(get_db)):
    """Twilio's waitUrl - called once almost immediately after a caller
    enters the queue (creating the QueueCallLog row) and then repeatedly
    while they wait. Returns hold TwiML, or <Leave/> once max_wait_seconds
    is exceeded - Twilio then falls through to whatever verbs follow the
    original <Enqueue> (the overflow node, already baked into that
    document by media.voice._flow_response)."""
    params = await media_service.verify_twilio_webhook(request)
    queue_id = request.query_params.get("queue_id", "")
    queue = db.query(CallQueue).filter(CallQueue.id == queue_id).first()
    if queue is None:
        return Response(content=telecom.build_empty_response(), media_type="application/xml")

    call_sid = params.get("CallSid", "")
    to_number = request.query_params.get("to_number") or params.get("To")
    service.enter_or_get_log(db, queue.id, call_sid, params.get("From", ""), to_number)

    elapsed = int(params.get("QueueTime", "0") or "0")
    if service.has_exceeded_max_wait(queue, elapsed):
        return Response(content=telecom.build_leave_response(), media_type="application/xml")
    return Response(content=telecom.build_hold_response(), media_type="application/xml")


@webhook_router.post("/left")
async def queue_left(request: Request, db: Session = Depends(get_db)):
    """Twilio's <Enqueue> action callback - fires once the call truly
    leaves the queue for any reason (bridged+ended, hangup while waiting,
    or overflow via <Leave>)."""
    params = await media_service.verify_twilio_webhook(request)
    call_sid = params.get("CallSid", "")
    result = params.get("QueueResult", "")
    default_outcome = QueueCallOutcome.ANSWERED if result == "bridged" else (
        QueueCallOutcome.OVERFLOWED if result in ("leave", "redirected") else QueueCallOutcome.ABANDONED
    )
    service.finalize_log(db, call_sid, default_outcome)
    return Response(status_code=204)


@webhook_router.post("/agent-connect")
async def queue_agent_connect(request: Request, db: Session = Depends(get_db)):
    """place_call()'s twiml_url in pull_next_caller - Twilio requests this
    once the agent's phone actually answers. Bridges them into the real
    Twilio queue and marks the oldest still-open call in our own log as
    answered by this agent."""
    await media_service.verify_twilio_webhook(request)
    queue_id = request.query_params.get("queue_id", "")
    agent_user_id = request.query_params.get("agent_user_id", "")
    preferred_log_id = request.query_params.get("queue_call_log_id")
    service.mark_answered(db, queue_id, agent_user_id, preferred_log_id)
    return Response(content=telecom.build_dial_queue_response(service.twilio_queue_name(queue_id)), media_type="application/xml")


@webhook_router.post("/agent-call-ended")
async def queue_agent_call_ended(request: Request, db: Session = Depends(get_db)):
    """status_callback for the agent-facing call placed by pull_next_caller
    - once it ends (whether the queue-bridge call completed normally or the
    agent never answered at all), the agent enters wrap-up."""
    await media_service.verify_twilio_webhook(request)
    agent_user_id = request.query_params.get("agent_user_id", "")
    queue_id = request.query_params.get("queue_id", "")
    queue = db.query(CallQueue).filter(CallQueue.id == queue_id).first()
    wrap_up_seconds = queue.wrap_up_seconds if queue else 30
    service.start_wrap_up(db, agent_user_id, wrap_up_seconds)
    return Response(status_code=204)
