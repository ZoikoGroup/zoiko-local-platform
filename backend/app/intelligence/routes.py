"""
AI voicemail summaries — transcribes a voicemail recording (Groq Whisper) and
generates a short summary (Groq LLM). Every state change is audited.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.integrations.llm.groq import LLMError
from app.integrations.telecom.twilio import TelecomError
from app.integrations.transcription.groq import TranscriptionError
from app.intelligence import service
from app.numbering.identity.models import User

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


@router.post("/voicemails/{voicemail_id}/summarize", status_code=status.HTTP_201_CREATED)
def summarize_voicemail(
    voicemail_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        record = service.summarize_voicemail(db, current_user.account_id, voicemail_id)
    except service.SummaryAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.ConsentRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except (TelecomError, TranscriptionError, LLMError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "id": record.id,
        "source_type": record.source_type.value,
        "transcript": record.transcript,
        "summary": record.summary,
        "model_version": record.model_version,
        "disclaimer": service.AI_DISCLAIMER,
    }


@router.post("/calls/{call_id}/summarize", status_code=status.HTTP_201_CREATED)
def summarize_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        record = service.summarize_call(db, current_user.account_id, call_id)
    except service.SummaryAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.ConsentRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.NotRecordedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except (TelecomError, TranscriptionError, LLMError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {
        "id": record.id,
        "source_type": record.source_type.value,
        "transcript": record.transcript,
        "summary": record.summary,
        "model_version": record.model_version,
        "disclaimer": service.AI_DISCLAIMER,
    }


@router.get("/summaries")
def list_summaries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    records = service.list_account_summaries(db, current_user.account_id)
    return [
        {
            "id": r.id,
            "source_type": r.source_type.value,
            "source_id": r.source_id,
            "summary": r.summary,
            "model_version": r.model_version,
            "created_at": r.created_at,
            "disclaimer": service.AI_DISCLAIMER,
        }
        for r in records
    ]
