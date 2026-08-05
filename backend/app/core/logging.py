"""Structured (JSON) logging setup - no new dependency (a small stdlib
Formatter, not structlog), so the 4 existing logging.getLogger("zoiko.*")
call sites across the codebase get JSON output for free with no call-site
changes. Adds trace_id/span_id when OTel tracing is enabled, so a log line
can be correlated back to the request span that produced it.
"""

import json
import logging
from datetime import datetime, timezone

from opentelemetry import trace

from app.core.config import settings


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        if settings.otel_enabled:
            span_context = trace.get_current_span().get_span_context()
            if span_context.is_valid:
                entry["trace_id"] = format(span_context.trace_id, "032x")
                entry["span_id"] = format(span_context.span_id, "016x")

        return json.dumps(entry)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.setLevel(settings.log_level)
    root.handlers = [handler]
