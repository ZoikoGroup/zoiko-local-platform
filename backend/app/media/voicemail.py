"""
Voicemail — Stage 5. `voice.py`'s incoming-call handler sends recognized
numbers to Twilio's <Record> verb; this is where the recording lands once
Twilio finishes processing it.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.integrations.telecom.twilio import TelecomError
from app.media import service as media_service
from app.numbering.identity.models import User

router = APIRouter(prefix="/media/voicemail", tags=["voicemail"])


@router.post("/recording-complete")
async def recording_complete(request: Request, db: Session = Depends(get_db)):
    params = await media_service.verify_twilio_webhook(request)

    to_number = params.get("To", "")
    owner = media_service.find_number_owner(db, to_number)
    if owner is not None:
        duration_raw = params.get("RecordingDuration")
        media_service.record_voicemail(
            db,
            account_id=owner.account_id,
            phone_number_id=owner.id,
            from_number=params.get("From", ""),
            recording_url=params.get("RecordingUrl", ""),
            duration=int(duration_raw) if duration_raw else None,
        )
    return Response(status_code=204)


@router.get("")
async def list_voicemails(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    voicemails = media_service.list_account_voicemails(db, current_user)
    return [
        {
            "id": v.id,
            "from": v.from_number,
            "recording_url": media_service.public_recording_url(v.recording_url),
            "duration": v.duration,
            "created_at": v.created_at,
        }
        for v in voicemails
    ]


@router.get("/{voicemail_id}/recording")
async def get_voicemail_recording(
    voicemail_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Streams the recording audio through this backend instead of handing
    the frontend Twilio's raw recording_url - that URL requires Twilio's
    own account credentials to fetch, which is why opening it directly in
    a browser prompts for a login instead of playing audio."""
    try:
        content, content_type = media_service.get_voicemail_recording_media(db, current_user, voicemail_id)
    except media_service.CallAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return Response(content=content, media_type=content_type)
