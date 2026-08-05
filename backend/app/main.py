from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.audit.routes import router as audit_router
from app.compliance.routes import router as compliance_router
from app.consent.routes import router as consent_router
from app.core.config import settings
from app.core.database import engine
from app.core.rate_limit import limiter
from app.core.startup_checks import assert_jwt_secret_is_configured, parse_allowed_origins
from app.intelligence.routes import router as intelligence_router
from app.media.receptionist import router as receptionist_router
from app.media.video import router as video_router
from app.media.voice import router as voice_router
from app.media.voicemail import router as voicemail_router
from app.numbering.identity.routes import router as identity_router
from app.numbering.identity.team_routes import router as team_router
from app.numbering.numbers.routes import router as numbers_router
from app.notifications.routes import router as notifications_router
from app.ops.routes import router as ops_router
from app.porting.routes import router as porting_router
from app.retention.routes import router as retention_router
from app.risk.routes import router as risk_router
from app.staff.routes import router as staff_router
from app.usage.routes import router as usage_router

app = FastAPI(title="Zoiko Local API")
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

# Fail fast rather than boot insecurely - see app/core/startup_checks.py.
assert_jwt_secret_is_configured(settings.environment, settings.jwt_secret_key)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_allowed_origins(settings.allowed_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(identity_router)
app.include_router(team_router)
app.include_router(audit_router)
app.include_router(compliance_router)
app.include_router(consent_router)
app.include_router(staff_router)
app.include_router(voice_router)
app.include_router(voicemail_router)
app.include_router(video_router)
app.include_router(receptionist_router)
app.include_router(numbers_router)
app.include_router(intelligence_router)
app.include_router(retention_router)
app.include_router(notifications_router)
app.include_router(risk_router)
app.include_router(usage_router)
app.include_router(ops_router)
app.include_router(porting_router)


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
