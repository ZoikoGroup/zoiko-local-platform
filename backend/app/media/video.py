"""
Video Routing — 1:1 and small-group video calling via LiveKit (Provider
Gateway: app.integrations.video.livekit). Every route requires auth and is
scoped to the caller's account_id; every state change is audited.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user, require_writer
from app.core.rate_limit import limiter
from app.integrations.video.livekit import VideoError, verify_webhook_event
from app.media import service as media_service
from app.numbering.identity.models import User

router = APIRouter(prefix="/media/video", tags=["video"])


class JoinTokenRequest(BaseModel):
    display_name: str


class GuestJoinTokenRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)


class CreateRoomRequest(BaseModel):
    confidential: bool = False


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
async def create_room(
    body: CreateRoomRequest = CreateRoomRequest(),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        session = await media_service.create_video_session(
            db, current_user.account_id, current_user.id, confidential=body.confidential
        )
    except VideoError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "room_name": session.room_name,
        "status": session.status.value,
        "confidential": session.confidential,
    }


@router.post("/rooms/{room_name}/token")
async def join_room(
    room_name: str,
    body: JoinTokenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        token = media_service.generate_video_join_token(
            db, current_user.account_id, room_name, current_user.id, body.display_name
        )
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    # The LiveKit client SDK (browser) connects directly to this URL with the
    # token above - returned here so the frontend doesn't need its own
    # separate copy of the same LiveKit project URL configured.
    return {"token": token, "url": settings.livekit_url}


@router.post("/rooms/{room_name}/guest-token")
@limiter.limit("10/minute")
async def guest_join_room(
    request: Request,
    room_name: str,
    body: GuestJoinTokenRequest,
    db: Session = Depends(get_db),
):
    """Deliberately no auth dependency - lets an external guest request to
    join via a shared link + their name only, no Zoiko account. Doesn't
    return a token directly - lands the guest in the waiting room (see
    .../waiting/{waiting_id}) until the host admits them. Rate-limited per
    IP like the login endpoints, since this is the one video route anyone
    on the internet can call without a token."""
    try:
        waiting_guest = media_service.request_guest_join(db, room_name, body.display_name)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"waiting_id": waiting_guest.id}


@router.get("/rooms/{room_name}/waiting/{waiting_id}")
@limiter.limit("60/minute")
async def guest_waiting_status(
    request: Request,
    room_name: str,
    waiting_id: str,
    db: Session = Depends(get_db),
):
    """Polled by the guest's browser (no auth - the guest has no account)
    every couple of seconds while waiting for the host to respond. A
    looser rate limit than the join request itself, since normal waiting
    behavior means several polls per minute, not an abuse pattern."""
    try:
        result = media_service.check_waiting_status(db, room_name, waiting_id)
    except (media_service.VideoSessionAuthorizationError, media_service.WaitingGuestNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {**result, "url": settings.livekit_url if result["token"] else None}


@router.get("/rooms/{room_name}/waiting")
async def list_waiting_guests(
    room_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    """Host-only - the waiting room list a host polls while in a call."""
    try:
        waiting_guests = media_service.list_waiting_guests(db, current_user, room_name)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return [
        {"id": g.id, "display_name": g.display_name, "created_at": g.created_at} for g in waiting_guests
    ]


@router.post("/rooms/{room_name}/waiting/{waiting_id}/admit")
async def admit_waiting_guest(
    room_name: str,
    waiting_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        media_service.admit_waiting_guest(db, current_user, room_name, waiting_id)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except media_service.WaitingGuestNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"admitted": True}


@router.post("/rooms/{room_name}/waiting/{waiting_id}/deny")
async def deny_waiting_guest(
    room_name: str,
    waiting_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        media_service.deny_waiting_guest(db, current_user, room_name, waiting_id)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except media_service.WaitingGuestNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"denied": True}


@router.post("/rooms/{room_name}/end")
async def end_room(
    room_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        session = await media_service.end_video_session(db, current_user, room_name)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except VideoError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"room_name": session.room_name, "status": session.status.value}


@router.post("/rooms/{room_name}/recording/start")
async def start_recording(
    room_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        session = await media_service.start_video_recording(db, current_user, room_name)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except media_service.RecordingConsentRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except media_service.ConfidentialModeRecordingBlockedError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except VideoError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"room_name": session.room_name, "recording": True}


@router.post("/webhook")
async def livekit_webhook(request: Request, db: Session = Depends(get_db)):
    """Requires the webhook URL to be configured in the LiveKit Cloud project
    dashboard, pointing at this route — that's a one-time dashboard setting,
    not something this code can register itself."""
    body = await request.body()
    auth_token = request.headers.get("Authorization", "")
    try:
        event = verify_webhook_event(body.decode(), auth_token)
    except VideoError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    media_service.handle_video_webhook_event(db, event)
    return Response(status_code=204)


@router.get("/rooms")
async def list_rooms(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sessions = media_service.list_account_video_sessions(db, current_user.account_id)
    return [
        {
            "room_name": s.room_name,
            "status": s.status.value,
            "started_at": s.started_at,
            "ended_at": s.ended_at,
            "recording_in_progress": s.recording_egress_id is not None and s.recording_url is None,
            "recording_url": media_service.get_recording_download_url(s),
            "participant_minutes": media_service.get_participant_minutes(db, s.id),
            "confidential": s.confidential,
        }
        for s in sessions
    ]


@router.get("/usage")
async def get_usage(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Roadmap doc's usage-metering requirement: total participant-minutes
    across every video session this account has had - a future ZoikoNex
    rating input, not itself a billing charge."""
    return {"participant_minutes": media_service.get_account_video_usage_minutes(db, current_user.account_id)}
