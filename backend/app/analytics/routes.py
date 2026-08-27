import csv
import io

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.analytics import service
from app.analytics.schemas import AnalyticsOverviewResponse
from app.core.database import get_db
from app.core.deps import require_admin, require_entitlement
from app.numbering.identity.models import User

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", response_model=AnalyticsOverviewResponse)
def get_overview(
    days: int = Query(service.DEFAULT_RANGE_DAYS, ge=1, le=service.MAX_RANGE_DAYS),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    _entitlement: User = Depends(require_entitlement("reporting.advanced")),
):
    # Owner/Admin only - same sensitivity as usage/routes.py's list_usage,
    # this is account-wide business activity, not a per-user view.
    return service.get_overview(db, current_user.account_id, days)


@router.get("/export.csv")
def export_csv(
    days: int = Query(service.DEFAULT_RANGE_DAYS, ge=1, le=service.MAX_RANGE_DAYS),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
    _entitlement: User = Depends(require_entitlement("reporting.advanced")),
):
    overview = service.get_overview(db, current_user.account_id, days)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "calls", "call_minutes", "video_minutes", "messages"])
    for point in overview["daily"]:
        writer.writerow([point["date"], point["calls"], point["call_minutes"], point["video_minutes"], point["messages"]])
    buffer.seek(0)

    filename = f"zoiko-analytics-{days}d.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
