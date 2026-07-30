from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.schemas import AuditEventResponse
from app.core.database import get_db
from app.core.deps import require_admin
from app.numbering.identity.models import User

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=list[AuditEventResponse])
def list_events(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
):
    return (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc())
        .limit(200)
        .all()
    )
