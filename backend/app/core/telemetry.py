"""OpenTelemetry setup - tracing + metrics for the FastAPI app, the
SQLAlchemy engine, and outbound httpx calls made by every integrations/
Provider Gateway. Off by default (OTEL_ENABLED=false), the same
"blank/false disables" pattern as KAFKA_BOOTSTRAP_SERVERS, so pytest and
local dev are unaffected unless explicitly opted in. When enabled with no
OTEL_EXPORTER_OTLP_ENDPOINT configured, spans/metrics print to the console
instead of requiring a real OTel collector to exist.
"""

import logging

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from app.core.config import settings

logger = logging.getLogger("zoiko.telemetry")

_initialized = False


def setup_telemetry(app, engine) -> None:
    """No-op unless settings.otel_enabled. Guarded by a module-level flag so
    it's safe to call more than once - app.main is only imported once per
    process in practice, but this protects against any future double-init
    (e.g. a test importing it directly)."""
    global _initialized
    if not settings.otel_enabled or _initialized:
        return

    resource = Resource.create({"service.name": settings.otel_service_name, "environment": settings.environment})

    tracer_provider = TracerProvider(resource=resource)
    span_exporter = (
        OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        if settings.otel_exporter_otlp_endpoint else ConsoleSpanExporter()
    )
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    metric_exporter = (
        OTLPMetricExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        if settings.otel_exporter_otlp_endpoint else ConsoleMetricExporter()
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[PeriodicExportingMetricReader(metric_exporter)])
    metrics.set_meter_provider(meter_provider)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument(engine=engine)
    HTTPXClientInstrumentor().instrument()

    _initialized = True
    logger.info(
        "OpenTelemetry initialized (service=%s, endpoint=%s)",
        settings.otel_service_name, settings.otel_exporter_otlp_endpoint or "console",
    )


def shutdown_telemetry() -> None:
    if not _initialized:
        return
    trace.get_tracer_provider().shutdown()
    metrics.get_meter_provider().shutdown()
