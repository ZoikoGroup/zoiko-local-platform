import traceback as traceback_module
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.observability.models import ErrorEvent


def record_error_event(
    *,
    request_id: str,
    method: str,
    path: str,
    status_code: int,
    exception: BaseException | None = None,
    account_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Best-effort - failing to log an error must never cause a second
    failure on top of the first one. Uses its own DB session rather than the
    request's: by the time this runs, the request's own session may be
    mid-rollback or in a broken transactional state (e.g. right after a
    DBAPIError), which is exactly the scenario this most needs to survive.
    """
    db = SessionLocal()
    try:
        db.add(
            ErrorEvent(
                request_id=request_id,
                method=method,
                path=path,
                status_code=status_code,
                exception_type=type(exception).__name__ if exception is not None else None,
                exception_message=str(exception) if exception is not None else None,
                traceback="".join(traceback_module.format_exception(exception)) if exception is not None else None,
                account_id=account_id,
                user_id=user_id,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def list_recent_errors(db: Session, limit: int = 100) -> list[ErrorEvent]:
    return db.query(ErrorEvent).order_by(ErrorEvent.created_at.desc()).limit(limit).all()


def get_error_event(db: Session, error_id: str) -> ErrorEvent | None:
    return db.query(ErrorEvent).filter(ErrorEvent.id == error_id).first()


def error_counts_by_type(db: Session, hours: int = 24) -> list[dict]:
    """Grouped summary for a staff dashboard - "is one thing failing
    repeatedly" is more actionable than a flat list of hundreds of rows."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(
            ErrorEvent.exception_type,
            ErrorEvent.path,
            ErrorEvent.status_code,
            sa.func.count().label("count"),
        )
        .filter(ErrorEvent.created_at >= window_start)
        .group_by(ErrorEvent.exception_type, ErrorEvent.path, ErrorEvent.status_code)
        .order_by(sa.desc("count"))
        .all()
    )
    return [
        {"exception_type": r[0], "path": r[1], "status_code": r[2], "count": r[3]}
        for r in rows
    ]
