import time

import sqlalchemy as sa
from sqlalchemy.orm import Session
from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.integrations.embeddings import cohere as cohere_embeddings
from app.integrations.kyc import stripe_identity
from app.integrations.llm import groq as groq_llm
from app.integrations.notifications import email as resend_email
from app.integrations.storage import s3
from app.integrations.telecom import twilio as telecom
from app.integrations.video import livekit as video
from app.ops.models import SyntheticCheckRun


async def get_provider_statuses() -> list[dict]:
    """Roadmap doc's "Admin operations... provider status" - real
    reachability checks, not just "is an env var set." Each check hits the
    real provider (cheapest call available), so this reports what's
    actually true right now, not what should be true if config were
    correct."""
    livekit_status = await video.health_check()
    checks = [
        ("twilio", telecom.health_check()),
        ("livekit", livekit_status),
        ("groq", groq_llm.health_check()),
        ("stripe_identity", stripe_identity.health_check()),
        ("resend", resend_email.health_check()),
        ("storage_s3", s3.health_check()),
        ("cohere", cohere_embeddings.health_check()),
    ]
    return [{"name": name, **result} for name, result in checks]


_PUBLIC_COMPONENT_NAMES = {
    "twilio": "Calling & SMS",
    "livekit": "Video",
    "groq": "AI Receptionist & Call Summaries",
    "stripe_identity": "Identity Verification",
    "resend": "Email Notifications",
    "storage_s3": "Recording Storage",
    "cohere": "Semantic Search",
}

_PUBLIC_STATUS_CACHE_TTL_SECONDS = 30
_public_status_cache: dict | None = None
_public_status_cache_at: float = 0.0


async def get_public_status() -> dict:
    """Customer-facing 'live service & uptime status' page the marketing
    site links to. Reuses the same real provider health checks as the
    staff-only /ops/provider-status, but collapses them into named
    components with a plain operational/degraded status - never exposes
    provider identity or raw error detail publicly (that stays behind staff
    auth). Cached briefly since this endpoint takes no auth and would
    otherwise let public traffic hammer real provider APIs on every
    pageview."""
    global _public_status_cache, _public_status_cache_at
    now = time.time()
    if _public_status_cache is not None and (now - _public_status_cache_at) < _PUBLIC_STATUS_CACHE_TTL_SECONDS:
        return _public_status_cache

    providers = await get_provider_statuses()
    components = [
        {
            "name": _PUBLIC_COMPONENT_NAMES[provider["name"]],
            "status": "operational" if provider["configured"] and provider["ok"] else "degraded",
        }
        for provider in providers
        if provider["name"] in _PUBLIC_COMPONENT_NAMES
    ]
    overall = "operational" if all(c["status"] == "operational" for c in components) else "degraded"

    result = {"overall": overall, "components": components}
    _public_status_cache = result
    _public_status_cache_at = now
    return result


def _check_database_connectivity(db: Session) -> dict:
    start = time.perf_counter()
    try:
        db.execute(sa.text("SELECT 1"))
        return {"success": True, "duration_ms": (time.perf_counter() - start) * 1000, "detail": None}
    except Exception as e:
        return {"success": False, "duration_ms": (time.perf_counter() - start) * 1000, "detail": str(e)}


def _check_twilio_webhook_signature_pipeline() -> dict:
    """Proves the real webhook signature validator would accept a
    genuinely-signed Twilio request right now - a stale or misconfigured
    TWILIO_AUTH_TOKEN would otherwise silently break every real inbound
    call/recording webhook, with no error until an actual call came in and
    got rejected. Self-signs a synthetic payload with our own configured
    secret (the exact one Twilio would sign with) and round-trips it
    through the real validator (app.integrations.telecom.twilio.
    validate_webhook_signature) - no network call and no real Twilio
    request needed, so this works even though the Twilio account here is
    trial-only with no real number to place an actual test call to."""
    start = time.perf_counter()
    if not settings.twilio_auth_token:
        return {
            "success": False, "duration_ms": (time.perf_counter() - start) * 1000,
            "detail": "TWILIO_AUTH_TOKEN is not configured",
        }
    try:
        validator = RequestValidator(settings.twilio_auth_token)
        url = f"{settings.public_base_url or 'https://synthetic-check.invalid'}/media/voice/incoming"
        params = {"CallSid": "CAsynthetic00000000000000000000", "From": "+15005550006", "To": "+15005550001"}
        signature = validator.compute_signature(url, params)
        accepted = telecom.validate_webhook_signature(url, params, signature)
        detail = None if accepted else "A signature computed with our own configured secret was rejected"
        return {"success": accepted, "duration_ms": (time.perf_counter() - start) * 1000, "detail": detail}
    except Exception as e:
        return {"success": False, "duration_ms": (time.perf_counter() - start) * 1000, "detail": str(e)}


async def run_synthetic_checks(db: Session) -> list[SyntheticCheckRun]:
    """Roadmap Month 5 launch-readiness gate: "synthetic call monitoring" -
    see SyntheticCheckRun's docstring for exactly what's covered (database
    connectivity, the webhook signature pipeline, and reachability of every
    *configured* provider - unconfigured providers are skipped, not
    reported as failing, since "not set up in this environment" isn't a
    monitoring alert). Persists every run for trend/history visibility via
    list_synthetic_check_runs, not just the latest result."""
    results: dict[str, dict] = {
        "database_connectivity": _check_database_connectivity(db),
        "twilio_webhook_signature_pipeline": _check_twilio_webhook_signature_pipeline(),
    }
    for provider in await get_provider_statuses():
        if not provider["configured"]:
            continue
        results[f"provider_reachability_{provider['name']}"] = {
            "success": provider["ok"], "duration_ms": 0.0, "detail": provider.get("detail"),
        }

    runs = []
    for check_name, result in results.items():
        run = SyntheticCheckRun(
            check_name=check_name, success=result["success"],
            duration_ms=result["duration_ms"], detail=result["detail"],
        )
        db.add(run)
        runs.append(run)
    db.commit()
    for run in runs:
        db.refresh(run)
    return runs


def list_synthetic_check_runs(
    db: Session, *, check_name: str | None = None, limit: int = 200
) -> list[SyntheticCheckRun]:
    query = db.query(SyntheticCheckRun)
    if check_name:
        query = query.filter(SyntheticCheckRun.check_name == check_name)
    return query.order_by(SyntheticCheckRun.created_at.desc()).limit(limit).all()


def get_synthetic_check_summary(db: Session) -> dict:
    """The "is everything healthy right now" view - most recent run per
    check_name, plus an overall pass/fail. Distinct from
    list_synthetic_check_runs's full history."""
    names = [row[0] for row in db.query(SyntheticCheckRun.check_name).distinct().all()]
    latest = []
    for name in names:
        run = (
            db.query(SyntheticCheckRun)
            .filter(SyntheticCheckRun.check_name == name)
            .order_by(SyntheticCheckRun.created_at.desc())
            .first()
        )
        if run is not None:
            latest.append(run)
    overall_healthy = all(run.success for run in latest) if latest else True
    return {"overall_healthy": overall_healthy, "checks": latest}
