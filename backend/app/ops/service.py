import time

from app.integrations.embeddings import cohere as cohere_embeddings
from app.integrations.kyc import stripe_identity
from app.integrations.llm import groq as groq_llm
from app.integrations.notifications import email as resend_email
from app.integrations.storage import s3
from app.integrations.telecom import twilio as telecom
from app.integrations.video import livekit as video


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
