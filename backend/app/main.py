import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.analytics.routes import router as analytics_router
from app.apikeys.routes import router as apikeys_router
from app.audit.routes import router as audit_router
from app.billing.routes import router as billing_router
from app.compliance.routes import router as compliance_router
from app.compliance.routes import staff_router as compliance_staff_router
from app.compliance.routes import webhook_router as compliance_webhook_router
from app.consent.routes import router as consent_router
from app.contacts.routes import router as contacts_router
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.core.error_logging import ErrorLoggingMiddleware
from app.core.deps import require_paid_or_read_only
from app.core.errors import EntitlementError
from app.core.logging import configure_logging
from app.crm.routes import router as crm_router
from app.core.rate_limit import limiter
from app.core.startup_checks import (
    assert_jwt_secret_is_configured,
    parse_allowed_origins,
    warn_if_db_connection_budget_is_risky,
)
from app.core.telemetry import setup_telemetry, shutdown_telemetry
from app.intelligence.routes import router as intelligence_router
from app.media.receptionist import router as receptionist_router
from app.media.video import public_router as video_public_router
from app.media.video import router as video_router
from app.media.voice import router as voice_router
from app.media.voicemail import router as voicemail_router
from app.messaging.routes import router as messaging_router
from app.messaging.routes import webhook_router as messaging_webhook_router
from app.numbering.identity.routes import router as identity_router
from app.numbering.identity.team_routes import router as team_router
from app.numbering.numbers.routes import router as numbers_router
from app.notifications.routes import router as notifications_router
from app.ops.routes import router as ops_router
from app.porting.routes import router as porting_router
from app.public_api.routes import router as public_api_router
from app.queues.routes import router as queues_router
from app.queues.routes import webhook_router as queues_webhook_router
from app.retention.routes import router as retention_router
from app.risk.routes import router as risk_router
from app.routing.routes import router as call_flows_router
from app.staff.routes import router as staff_router
from app.staff.service import bootstrap_initial_super_admin
from app.usage.routes import router as usage_router
from app.webhooks.routes import router as webhooks_router

# Runs once at import time (not per app-instance), before any FastAPI app
# exists - configure_logging() itself has nothing to do with app.state.
configure_logging()
_startup_logger = logging.getLogger("zoiko.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Idempotent (see bootstrap_initial_super_admin's own docstring) - safe
    # to attempt on every boot. Wrapped defensively so a transient DB
    # hiccup at startup (seen for real against this project's Neon
    # instance) delays staff bootstrap by one restart instead of crash-
    # looping the whole API over a non-critical, self-healing step.
    try:
        db = SessionLocal()
        try:
            bootstrap_initial_super_admin(db)
        finally:
            db.close()
    except Exception:
        _startup_logger.exception("initial super admin bootstrap failed - will retry next boot")
    yield
    shutdown_telemetry()


# /docs, /redoc, and the raw OpenAPI schema enumerate every internal route
# (staff/admin endpoints included) - fine to browse in development, an
# unnecessary reconnaissance gift to anyone on the internet once this is a
# real deployment. None of the three exist outside development.
_docs_enabled = settings.environment == "development"
app = FastAPI(
    title="Zoiko Local API",
    lifespan=lifespan,
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(DBAPIError)
async def database_unavailable_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    """Chaos-testing finding: a plain route with no DB-specific try/except
    (most list/read endpoints) let a real Postgres outage bubble up as a
    generic, unhandled 500 - technically not a crash (FastAPI's default
    debug=False already keeps the traceback out of the response), but a
    503 correctly tells the caller this is transient and retry-able, which
    a bare 500 does not. Registered on DBAPIError (not the narrower
    OperationalError) since a pool-exhaustion or driver-level failure can
    surface as either, and both mean the same thing to a client: try again
    shortly, not "your request was invalid."
    """
    return JSONResponse(status_code=503, content={"detail": "Service temporarily unavailable - please try again shortly."})


@app.exception_handler(EntitlementError)
async def entitlement_error_handler(request: Request, exc: EntitlementError) -> JSONResponse:
    """Single conversion point for commercial/entitlement domain errors -
    replaces the ~12 previously-duplicated per-route try/except blocks that
    each mapped one of these exceptions to HTTPException(detail=str(e))
    with no machine-readable code. Additive to the response body (`detail`
    unchanged, `code` new) - no existing client breaks."""
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})

# Fail fast rather than boot insecurely - see app/core/startup_checks.py.
assert_jwt_secret_is_configured(settings.environment, settings.jwt_secret_key)
warn_if_db_connection_budget_is_risky(
    settings.db_pool_size, settings.db_max_overflow, settings.web_concurrency,
    settings.db_connection_budget_warning_threshold,
)

# Added before CORSMiddleware so CORS ends up outermost (last added wraps
# first) - even an error response this middleware logs still needs its CORS
# headers added by the layer above it.
app.add_middleware(ErrorLoggingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Must run before the app ever serves a request (including the lifespan
# protocol itself) - Starlette caches its middleware stack on first ASGI
# call, so instrumenting from inside the lifespan handler above would be
# one request too late for FastAPIInstrumentor's ASGI-level wrapping to
# actually take effect.
setup_telemetry(app, engine)

# Trial-account gate (app.core.deps.require_paid_or_read_only) - applied
# router-wide via `dependencies=` rather than per-route, so it covers every
# current and future route in these feature routers automatically. Only
# blocks non-GET requests for a TRIALING account; Home's own read-only
# stat cards keep working since GET is always exempt. NOT applied to
# identity/team/consent/billing/usage/notifications/webhooks/staff/
# public_api/audit/ops/retention/risk/crm/apikeys, or to
# messaging_webhook_router/queues_webhook_router/video_public_router -
# those are either account-setup basics every account needs regardless of
# plan (inviting a teammate, granting AI-processing consent - confirmed
# live: several existing tests' shared signup helpers call these as
# ordinary setup steps unrelated to what they're testing, not something a
# trial paywall should ever interrupt), the upgrade path itself (billing),
# or carry no customer JWT at all (provider webhooks, video's guest
# endpoints - see video.py's own comment on why those had to be split into
# a separate router first).
_TRIAL_GATE = [Depends(require_paid_or_read_only)]

app.include_router(identity_router)
app.include_router(team_router)
app.include_router(audit_router)
app.include_router(billing_router)
app.include_router(compliance_router, dependencies=_TRIAL_GATE)
app.include_router(compliance_staff_router)
app.include_router(compliance_webhook_router)
app.include_router(consent_router)
app.include_router(contacts_router, dependencies=_TRIAL_GATE)
app.include_router(staff_router)
app.include_router(voice_router, dependencies=_TRIAL_GATE)
app.include_router(voicemail_router, dependencies=_TRIAL_GATE)
app.include_router(video_router, dependencies=_TRIAL_GATE)
app.include_router(video_public_router)
app.include_router(receptionist_router, dependencies=_TRIAL_GATE)
app.include_router(numbers_router, dependencies=_TRIAL_GATE)
app.include_router(intelligence_router, dependencies=_TRIAL_GATE)
app.include_router(retention_router)
app.include_router(notifications_router)
app.include_router(risk_router)
app.include_router(usage_router)
app.include_router(ops_router)
app.include_router(porting_router, dependencies=_TRIAL_GATE)
app.include_router(call_flows_router, dependencies=_TRIAL_GATE)
app.include_router(queues_router, dependencies=_TRIAL_GATE)
app.include_router(queues_webhook_router)
app.include_router(messaging_router, dependencies=_TRIAL_GATE)
app.include_router(messaging_webhook_router)
app.include_router(analytics_router, dependencies=_TRIAL_GATE)
app.include_router(webhooks_router)
app.include_router(apikeys_router)
app.include_router(public_api_router)
app.include_router(crm_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/db")
async def health_db():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "connected"}
    except Exception as e:
        return {"status": "error", "database": "unreachable", "detail": str(e)}
