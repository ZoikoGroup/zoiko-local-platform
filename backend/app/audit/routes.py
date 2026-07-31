from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.audit.schemas import AuditEventResponse
from app.core.database import get_db
from app.core.deps import get_current_staff
from app.staff.models import PlatformStaff

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=list[AuditEventResponse])
def list_events(
    db: Session = Depends(get_db),
    _staff: PlatformStaff = Depends(get_current_staff),
):
    # Lists events across ALL accounts - this is why it's staff-only,
    # not customer-admin: a customer must never see another customer's
    # audit trail, but Zoiko ops legitimately needs cross-account visibility.
    return (
        db.query(AuditEvent)
        .order_by(AuditEvent.created_at.desc())
        .limit(200)
        .all()
    )
