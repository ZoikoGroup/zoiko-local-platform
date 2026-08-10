import contextvars
import time as time_module
import traceback as traceback_module
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.observability.models import ErrorEvent, ProviderCallTrace

# Set by ErrorLoggingMiddleware for the lifetime of each request, read by
# trace_provider_call below - lets Provider Gateway modules (which don't
# take a `request` or `db` parameter) tag their traces with the request
# that triggered them, without threading either through every function
# signature. ContextVars propagate correctly across await points within
# the same request's asyncio task, which is exactly Starlette's model.
current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_request_id", default=None
)


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


def record_provider_call_trace(
    *, provider: str, operation: str, duration_ms: float, success: bool, error_detail: str | None = None
) -> None:
    """Best-effort, same rationale as record_error_event - a tracing failure
    must never break the actual provider call it's describing. Uses its own
    session since integration modules have no request-scoped session to
    reuse (they're plain functions with no `db` parameter, by design)."""
    db = SessionLocal()
    try:
        db.add(
            ProviderCallTrace(
                request_id=current_request_id.get(),
                provider=provider,
                operation=operation,
                duration_ms=duration_ms,
                success=success,
                error_detail=error_detail,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


class trace_provider_call:
    """Wrap one outbound Provider Gateway call:

        with trace_provider_call("twilio", "place_call"):
            call = _client().calls.create(**kwargs)

    Records latency and success/failure regardless of outcome and never
    swallows the original exception - the caller's own try/except (which
    translates the vendor exception into e.g. TelecomError) still runs
    exactly as before."""

    def __init__(self, provider: str, operation: str):
        self.provider = provider
        self.operation = operation

    def __enter__(self):
        self._start = time_module.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = round((time_module.perf_counter() - self._start) * 1000, 1)
        record_provider_call_trace(
            provider=self.provider,
            operation=self.operation,
            duration_ms=duration_ms,
            success=exc_type is None,
            error_detail=str(exc_val) if exc_val is not None else None,
        )
        return False


def list_recent_provider_traces(
    db: Session, *, provider: str | None = None, request_id: str | None = None, limit: int = 200
) -> list[ProviderCallTrace]:
    query = db.query(ProviderCallTrace)
    if provider:
        query = query.filter(ProviderCallTrace.provider == provider)
    if request_id:
        query = query.filter(ProviderCallTrace.request_id == request_id)
    return query.order_by(ProviderCallTrace.created_at.desc()).limit(limit).all()


def provider_call_latency_summary(db: Session, hours: int = 24) -> list[dict]:
    """Grouped by provider+operation - avg/max latency and failure count
    over the window, the actionable "what's slow or flaky right now" view
    for the staff console, same spirit as error_counts_by_type."""
    window_start = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = (
        db.query(
            ProviderCallTrace.provider,
            ProviderCallTrace.operation,
            sa.func.count().label("count"),
            sa.func.avg(ProviderCallTrace.duration_ms).label("avg_duration_ms"),
            sa.func.max(ProviderCallTrace.duration_ms).label("max_duration_ms"),
            sa.func.sum(sa.case((ProviderCallTrace.success.is_(False), 1), else_=0)).label("failure_count"),
        )
        .filter(ProviderCallTrace.created_at >= window_start)
        .group_by(ProviderCallTrace.provider, ProviderCallTrace.operation)
        .order_by(sa.desc("avg_duration_ms"))
        .all()
    )
    return [
        {
            "provider": r[0],
            "operation": r[1],
            "count": r[2],
            "avg_duration_ms": round(float(r[3]), 1) if r[3] is not None else 0.0,
            "max_duration_ms": round(float(r[4]), 1) if r[4] is not None else 0.0,
            "failure_count": r[5],
        }
        for r in rows
    ]


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
