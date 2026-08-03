"""
Video Routing — 1:1 and small-group video calling via LiveKit (Provider
Gateway: app.integrations.video.livekit). Every route requires auth and is
scoped to the caller's account_id; every state change is audited.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.integrations.video.livekit import VideoError, verify_webhook_event
from app.media import service as media_service
from app.numbering.identity.models import User

router = APIRouter(prefix="/media/video", tags=["video"])


class JoinTokenRequest(BaseModel):
    display_name: str


@router.post("/rooms", status_code=status.HTTP_201_CREATED)
async def create_room(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        session = await media_service.create_video_session(db, current_user.account_id, current_user.id)
    except VideoError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"room_name": session.room_name, "status": session.status.value}


@router.post("/rooms/{room_name}/token")
async def join_room(
    room_name: str,
    body: JoinTokenRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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


@router.post("/rooms/{room_name}/end")
async def end_room(
    room_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        session = await media_service.end_video_session(db, current_user.account_id, room_name)
    except media_service.VideoSessionAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except VideoError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"room_name": session.room_name, "status": session.status.value}


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
        }
        for s in sessions
    ]
