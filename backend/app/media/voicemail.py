"""
Voicemail — Stage 5. `voice.py`'s incoming-call handler sends recognized
numbers to Twilio's <Record> verb; this is where the recording lands once
Twilio finishes processing it.
"""

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
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
    voicemails = media_service.list_account_voicemails(db, current_user.account_id)
    return [
        {
            "id": v.id,
            "from": v.from_number,
            "recording_url": v.recording_url,
            "duration": v.duration,
            "created_at": v.created_at,
        }
        for v in voicemails
    ]
