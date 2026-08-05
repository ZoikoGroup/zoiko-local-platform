import json
import logging

from app.core import telemetry
from app.core.logging import _JsonFormatter


def test_setup_telemetry_is_a_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(telemetry.settings, "otel_enabled", False)
    monkeypatch.setattr(telemetry, "_initialized", False)

    telemetry.setup_telemetry(app=None, engine=None)  # must not raise even with garbage args

    assert telemetry._initialized is False


def test_setup_telemetry_initializes_once_when_enabled(monkeypatch):
    from fastapi import FastAPI
    from sqlalchemy import create_engine

    monkeypatch.setattr(telemetry.settings, "otel_enabled", True)
    monkeypatch.setattr(telemetry.settings, "otel_exporter_otlp_endpoint", "")  # console exporter path
    monkeypatch.setattr(telemetry, "_initialized", False)

    app = FastAPI()
    engine = create_engine("sqlite:///:memory:")

    telemetry.setup_telemetry(app, engine)
    assert telemetry._initialized is True

    # Second call must be a true no-op (no re-instrumentation, which would
    # raise since FastAPIInstrumentor refuses to instrument the same app twice).
    telemetry.setup_telemetry(app, engine)

    telemetry.shutdown_telemetry()
    telemetry._initialized = False  # reset so later tests see the default state


def test_json_formatter_produces_valid_json(monkeypatch):
    from app.core import logging as app_logging

    monkeypatch.setattr(app_logging.settings, "otel_enabled", False)

    formatter = _JsonFormatter()
    record = logging.LogRecord(
        name="zoiko.test", level=logging.INFO, pathname=__file__, lineno=1,
        msg="hello %s", args=("world",), exc_info=None,
    )
    output = formatter.format(record)

    parsed = json.loads(output)
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "zoiko.test"
    assert "trace_id" not in parsed  # otel disabled - no trace context to attach
