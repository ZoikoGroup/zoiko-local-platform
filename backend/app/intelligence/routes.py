"""
AI voicemail summaries — transcribes a voicemail recording (Groq Whisper) and
generates a short summary (Groq LLM). Every state change is audited.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.billing.service import EntitlementRequiredError
from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_writer
from app.integrations.llm.groq import LLMError
from app.integrations.storage.s3 import StorageError
from app.integrations.telecom.twilio import TelecomError
from app.integrations.transcription.groq import TranscriptionError
from app.intelligence import service
from app.intelligence.models import AIJobStatus
from app.numbering.identity.models import User
from app.ops.service import KillSwitchTrippedError
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/intelligence", tags=["intelligence"])


class EditSummaryRequest(BaseModel):
    summary: str


def _summary_response(record) -> dict:
    return {
        "id": record.id,
        "source_type": record.source_type.value,
        "source_id": record.source_id,
        "transcript": record.transcript,
        "summary": record.summary,
        "language": record.language,
        "urgency": record.urgency.value if record.urgency else None,
        "action_items": record.action_items or [],
        "suggested_follow_up": record.suggested_follow_up,
        "model_version": record.model_version,
        "created_at": record.created_at,
        "disclaimer": service.AI_DISCLAIMER,
        "original_summary": record.original_summary,
        "edited_at": record.edited_at,
        "edited_by_user_id": record.edited_by_user_id,
    }


@router.post("/voicemails/{voicemail_id}/summarize", status_code=status.HTTP_201_CREATED)
def summarize_voicemail(
    voicemail_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        record = service.summarize_voicemail(db, current_user, voicemail_id)
    except service.SummaryAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.ConsentRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except EntitlementRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": e.key, "current_plan": e.plan_code},
        ) from e
    # BillingSuspendedError no longer caught here - subclasses
    # EntitlementError, handled by the global entitlement_error_handler.
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (TelecomError, TranscriptionError, LLMError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _summary_response(record)


@router.post("/calls/{call_id}/summarize", status_code=status.HTTP_201_CREATED)
def summarize_call(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        record = service.summarize_call(db, current_user, call_id)
    except service.SummaryAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.ConsentRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except EntitlementRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": e.key, "current_plan": e.plan_code},
        ) from e
    except service.NotRecordedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # BillingSuspendedError no longer caught here - subclasses
    # EntitlementError, handled by the global entitlement_error_handler.
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (TelecomError, TranscriptionError, LLMError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _summary_response(record)


@router.post("/video-sessions/{room_name}/summarize", status_code=status.HTTP_201_CREATED)
def summarize_video_session(
    room_name: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        record = service.summarize_video_session(db, current_user, room_name)
    except service.SummaryAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except service.ConsentRequiredError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except EntitlementRequiredError as e:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "ENTITLEMENT_REQUIRED", "entitlement": e.key, "current_plan": e.plan_code},
        ) from e
    except service.NotRecordedError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    # BillingSuspendedError no longer caught here - subclasses
    # EntitlementError, handled by the global entitlement_error_handler.
    except KillSwitchTrippedError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except (StorageError, TranscriptionError, LLMError, service.AudioExtractionError) as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return _summary_response(record)


@router.patch("/summaries/{summary_id}")
def edit_summary(
    summary_id: str,
    payload: EditSummaryRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_writer),
):
    try:
        record = service.edit_summary(db, current_user, summary_id, payload.summary)
    except service.SummaryAuthorizationError as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    return _summary_response(record)


@router.get("/summaries")
def list_summaries(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    records = service.list_account_summaries(db, current_user)
    return [_summary_response(r) for r in records]


@router.get("/summaries/search")
def search_summaries(
    q: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not q.strip():
        return []
    records = service.search_account_summaries(db, current_user, q)
    return [_summary_response(r) for r in records]


@router.get("/jobs")
def list_ai_jobs(
    job_status: AIJobStatus | None = None,
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-only retry-lineage/failure-observability view (AIJob's
    docstring) - answers "why did this recording never get a summary"
    without grepping raw logs. Any staff role can view (diagnostic, same
    posture as /ops/traces)."""
    jobs = service.list_ai_jobs(db, job_status)
    return [
        {
            "id": j.id, "account_id": j.account_id, "source_type": j.source_type.value, "source_id": j.source_id,
            "status": j.status.value, "attempt_count": j.attempt_count, "last_error": j.last_error,
            "conversation_summary_id": j.conversation_summary_id,
            "created_at": j.created_at, "updated_at": j.updated_at,
        }
        for j in jobs
    ]
