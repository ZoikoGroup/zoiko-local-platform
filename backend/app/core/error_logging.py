import json
import logging
import sys
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from app.observability.service import current_request_id, record_error_event

# Deliberately plain stdout JSON (one line per request/error), not a
# third-party logging library - Fly.io (and any container platform) already
# captures stdout, so `fly logs` / `docker logs` gives structured, greppable
# request logs for free with zero extra infrastructure.
#
# Explicit handler + propagate=False, not just logging.getLogger(name) -
# uvicorn's own logging config only sets up handlers for its own
# "uvicorn"/"uvicorn.access"/"uvicorn.error" loggers, not the root logger,
# so an unconfigured logger here would have nowhere to send records at all
# (confirmed live: every request/error log line was silently dropped until
# this was added, while X-Request-ID and error_events rows worked fine -
# writing to the DB and writing to a log stream are independent failure
# modes).
request_logger = logging.getLogger("zoiko.requests")
request_logger.setLevel(logging.INFO)
if not request_logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    request_logger.addHandler(_handler)
    request_logger.propagate = False


class ErrorLoggingMiddleware(BaseHTTPMiddleware):
    """Structured request logging + self-hosted error monitoring (Roadmap
    Month 5 launch-readiness gate). Every request gets a request_id (also
    returned as the X-Request-ID header, so a user-reported issue can be
    correlated to a specific log line/error_event row) and a structured JSON
    log line. Every 5xx response - whether from a genuinely unhandled
    exception or one of our own `raise HTTPException(502/503, ...)` calls
    after a caught provider/DB failure - also gets a row in error_events
    (see app.observability) for a queryable history without a third-party
    APM account.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        # Populated by app.core.deps.get_current_user/get_current_staff if
        # this request authenticates successfully - absent (None) for
        # unauthenticated requests or ones that fail before auth resolves.
        request.state.account_id = None
        request.state.user_id = None

        # Lets Provider Gateway calls made anywhere downstream in this
        # request tag their traces with this request_id - see
        # app.observability.service.current_request_id. Reset in `finally`
        # so a value never leaks into whatever request reuses this task
        # next (ContextVar tokens are the sanctioned way to do this, not a
        # bare `.set(None)` afterward).
        request_id_token = current_request_id.set(request_id)
        try:
            start = time.perf_counter()
            try:
                response = await call_next(request)
            except Exception as exc:
                duration_ms = round((time.perf_counter() - start) * 1000, 1)
                record_error_event(
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=500,
                    exception=exc,
                    account_id=request.state.account_id,
                    user_id=request.state.user_id,
                )
                self._log(request, request_id, 500, duration_ms, error=str(exc))
                raise

            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            response.headers["X-Request-ID"] = request_id

            if response.status_code >= 500:
                record_error_event(
                    request_id=request_id,
                    method=request.method,
                    path=request.url.path,
                    status_code=response.status_code,
                    account_id=request.state.account_id,
                    user_id=request.state.user_id,
                )

            self._log(request, request_id, response.status_code, duration_ms)
            return response
        finally:
            current_request_id.reset(request_id_token)

    def _log(self, request: Request, request_id: str, status_code: int, duration_ms: float, error: str | None = None) -> None:
        entry = {
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": status_code,
            "duration_ms": duration_ms,
        }
        if error is not None:
            entry["error"] = error
        level = logging.ERROR if status_code >= 500 else logging.INFO
        request_logger.log(level, json.dumps(entry))
