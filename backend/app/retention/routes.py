from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin, require_capability
from app.numbering.identity.models import User
from app.retention import service
from app.retention.models import ArtifactType, ErasureRequestStatus
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/retention", tags=["retention"])


class SetRetentionPolicyRequest(BaseModel):
    retention_days: int = Field(ge=1)


class CreateErasureRequestRequest(BaseModel):
    notes: str | None = None


class ErasureRequestResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    account_id: str
    requested_by: str
    status: ErasureRequestStatus
    notes: str | None
    resolved_by: str | None
    resolution_notes: str | None
    created_at: datetime
    resolved_at: datetime | None


class ResolveErasureRequestRequest(BaseModel):
    status: ErasureRequestStatus
    resolution_notes: str | None = None


@router.get("/policies")
def list_policies(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_retention_policies(db, current_user.account_id)


@router.put("/policies/{artifact_type}")
def set_policy(
    artifact_type: ArtifactType,
    payload: SetRetentionPolicyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    try:
        policy = service.set_retention_policy(
            db,
            account_id=current_user.account_id,
            artifact_type=artifact_type,
            retention_days=payload.retention_days,
            actor=current_user.id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e
    return {"artifact_type": policy.artifact_type.value, "retention_days": policy.retention_days}


@router.post("/purge")
def purge(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    """Staff-only, manually triggered - there's no cron/scheduler in this
    app yet, so this is meant to be called by an external scheduled task
    (e.g. a daily OS-level cron hitting this endpoint) until real job
    infrastructure exists."""
    return service.purge_expired_recordings(db)


@router.post("/erasure-request", response_model=ErasureRequestResponse, status_code=status.HTTP_201_CREATED)
def create_erasure_request(
    payload: CreateErasureRequestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Architecture doc §10 "right-to-erasure workflow" - opens a staff-
    visible request; this does not itself delete anything (see
    resolve_erasure_request's docstring for why a DSAR needs human
    review, not an automatic delete button)."""
    return service.create_erasure_request(
        db, account_id=current_user.account_id, requested_by=current_user.id, notes=payload.notes,
    )


@router.get("/erasure-requests/me", response_model=list[ErasureRequestResponse])
def list_my_erasure_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return service.list_erasure_requests(db, account_id=current_user.account_id)


@router.get("/staff/erasure-requests", response_model=list[ErasureRequestResponse])
def list_all_erasure_requests(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    return service.list_erasure_requests(db)


@router.post("/staff/erasure-requests/{request_id}/resolve", response_model=ErasureRequestResponse)
def resolve_erasure_request(
    request_id: str,
    payload: ResolveErasureRequestRequest,
    db: Session = Depends(get_db),
    staff: PlatformStaff = Depends(require_capability("retention.resolve_erasure_requests")),
):
    try:
        return service.resolve_erasure_request(
            db, request_id, status=payload.status, resolution_notes=payload.resolution_notes, actor=staff.id,
        )
    except service.ErasureRequestNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.AccountUnderLegalHoldError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from e


@router.post("/calls/{call_id}/erase-content")
def erase_call_content(
    call_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Erases one specific call's recording and AI summary/transcript -
    e.g. it captured something personal that shouldn't be retained -
    without touching any other data on the account (unlike POST
    /retention/erasure-request, which is a whole-account, staff-reviewed
    request). The call itself stays in the account's call history as a
    bare entry (who/when/how long), just with no audio or AI-generated
    content attached anymore. Owner/Admin only, scoped to the caller's own
    account - can never reach another account's call."""
    from app.integrations.telecom.twilio import TelecomError

    try:
        return service.erase_single_call_content(db, current_user.account_id, call_id, actor=current_user.id)
    except service.CallNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except service.AccountUnderLegalHoldError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except TelecomError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
