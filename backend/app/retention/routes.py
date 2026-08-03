from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_staff, get_current_user, require_admin
from app.numbering.identity.models import User
from app.retention import service
from app.retention.models import ArtifactType
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/retention", tags=["retention"])


class SetRetentionPolicyRequest(BaseModel):
    retention_days: int = Field(ge=1)


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
