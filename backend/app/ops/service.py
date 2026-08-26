import time
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.audit.service import log_event
from app.core.config import settings
from app.events.service import publish_incident_declared, publish_incident_resolved, publish_kill_switch_changed
from app.integrations.cache.redis import cache_get, cache_set
from app.integrations.billing import stripe_checkout
from app.integrations.embeddings import cohere as cohere_embeddings
from app.integrations.kyc import stripe_identity
from app.integrations.llm import groq as groq_llm
from app.integrations.notifications import email as resend_email
from app.integrations.storage import s3
from app.integrations.telecom import twilio as telecom
from app.integrations.video import livekit as video
from app.notifications.service import (
    notify_incident_declared,
    notify_incident_resolved,
    notify_incident_update,
    notify_status_subscription_confirmed,
)
from app.ops.models import Incident, IncidentStatus, KillSwitchScope, PlatformKillSwitch, StatusSubscription, SyntheticCheckRun

# Maps a provider-status entry name to the (circuit_state accessor,
# failover_enabled setting) of the category it belongs to - only providers
# with failover infra built (see integrations/_shared/circuit_breaker.py)
# get these two extra fields; "cohere" (embeddings) has none yet.
_FAILOVER_INFO = {
    "twilio": (telecom.circuit_state, "telecom_failover_enabled"),
    "livekit": (video.circuit_state, "video_failover_enabled"),
    "groq": (groq_llm.circuit_state, "llm_failover_enabled"),
    "stripe_identity": (stripe_identity.circuit_state, "kyc_failover_enabled"),
    "resend": (resend_email.circuit_state, "email_failover_enabled"),
    "storage_s3": (s3.circuit_state, "storage_failover_enabled"),
}


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
        ("stripe_payments", stripe_checkout.health_check()),
        ("resend", resend_email.health_check()),
        ("storage_s3", s3.health_check()),
        ("cohere", cohere_embeddings.health_check()),
    ]
    statuses = []
    for name, result in checks:
        status = {"name": name, **result}
        if name in _FAILOVER_INFO:
            circuit_state_fn, failover_setting_name = _FAILOVER_INFO[name]
            status["circuit_state"] = circuit_state_fn()
            status["failover_enabled"] = getattr(settings, failover_setting_name)
        statuses.append(status)
    return statuses


_PUBLIC_COMPONENT_NAMES = {
    "twilio": "Calling & SMS",
    "livekit": "Video",
    "groq": "AI Receptionist & Call Summaries",
    "stripe_identity": "Identity Verification",
    "stripe_payments": "Number Purchase Payments",
    "resend": "Email Notifications",
    "storage_s3": "Recording Storage",
    "cohere": "Semantic Search",
}

_PUBLIC_STATUS_CACHE_KEY = "ops:public_status"
_PUBLIC_STATUS_CACHE_TTL_SECONDS = 30


async def get_public_status() -> dict:
    """Customer-facing 'live service & uptime status' page the marketing
    site links to. Reuses the same real provider health checks as the
    staff-only /ops/provider-status, but collapses them into named
    components with a plain operational/degraded status - never exposes
    provider identity or raw error detail publicly (that stays behind staff
    auth). Cached briefly since this endpoint takes no auth and would
    otherwise let public traffic hammer real provider APIs on every
    pageview.

    Redis-backed like every other cached read in this codebase, not a
    per-process module-level dict (what this used to be) - this app runs
    WEB_CONCURRENCY=4 uvicorn workers, so a process-local cache meant each
    worker independently re-ran the real provider health checks on its own
    30-second clock, up to 4x more often than the TTL implies, and could
    show a slightly different result depending on which worker answered a
    given request. Degrades to "no cache" like every other cache_get/
    cache_set call site if Redis is unavailable - never blocks this
    endpoint, just re-runs the real checks every time."""
    cached = cache_get(_PUBLIC_STATUS_CACHE_KEY)
    if cached is not None:
        return cached

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
    cache_set(_PUBLIC_STATUS_CACHE_KEY, result, ttl_seconds=_PUBLIC_STATUS_CACHE_TTL_SECONDS)
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
    request needed, so this works even though the Twilio  account here is
    trial-only with no real number to place an actual test call to."""
    start = time.perf_counter()
    if not settings.twilio_auth_token:
        return {
            "success": False, "duration_ms": (time.perf_counter() - start) * 1000,
            "detail": "TWILIO_AUTH_TOKEN is not configured",
        }
    try:
        url = f"{settings.public_base_url or 'https://synthetic-check.invalid'}/media/voice/incoming"
        params = {"CallSid": "CAsynthetic00000000000000000000", "From": "+15005550006", "To": "+15005550001"}
        signature = telecom.compute_webhook_signature(url, params)
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
    results: dict[str,  dict] = {
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


def _list_active_subscribers(db: Session) -> list:
    from app.numbering.identity.models import User

    account_ids = [
        row[0] for row in db.query(StatusSubscription.account_id)
        .filter(StatusSubscription.is_active.is_(True)).all()
    ]
    if not account_ids:
        return []
    return db.query(User).filter(User.account_id.in_(account_ids), User.email.isnot(None)).all()


def create_incident(db: Session, *, title: str, affected_service: str, impact_summary: str) -> Incident:
    incident = Incident(title=title, affected_service=affected_service, impact_summary=impact_summary)
    db.add(incident)
    db.commit()
    db.refresh(incident)
    publish_incident_declared(incident_id=incident.id, title=title, affected_service=affected_service)
    for user in _list_active_subscribers(db):
        notify_incident_declared(
            db, account_id=user.account_id, account_email=user.email,
            affected_service=affected_service, impact_summary=impact_summary,
        )
    return incident


class IncidentNotFoundError(Exception):
    """Raised when an incident id doesn't exist."""


class IncidentAlreadyResolvedError(Exception):
    """Raised when trying to update/resolve an incident that's already RESOLVED."""


def update_incident(
    db: Session, incident_id: str, *, status: IncidentStatus, impact_summary: str | None = None,
    mitigation_summary: str | None = None,
) -> Incident:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident is None:
        raise IncidentNotFoundError(f"No such incident {incident_id!r}")
    if incident.status == IncidentStatus.RESOLVED:
        raise IncidentAlreadyResolvedError(f"Incident {incident_id} is already resolved")

    incident.status = status
    if impact_summary is not None:
        incident.impact_summary = impact_summary
    if mitigation_summary is not None:
        incident.mitigation_summary = mitigation_summary
    db.commit()
    db.refresh(incident)
    for user in _list_active_subscribers(db):
        notify_incident_update(
            db, account_id=user.account_id, account_email=user.email, incident_reference=incident.id,
            status=incident.status.value, impact_summary=incident.impact_summary,
            mitigation_summary=incident.mitigation_summary,
        )
    return incident


def resolve_incident(db: Session, incident_id: str) -> Incident:
    incident = db.query(Incident).filter(Incident.id == incident_id).first()
    if incident is None:
        raise IncidentNotFoundError(f"No such incident {incident_id!r}")
    if incident.status == IncidentStatus.RESOLVED:
        raise IncidentAlreadyResolvedError(f"Incident {incident_id} is already resolved")

    incident.status = IncidentStatus.RESOLVED
    incident.resolved_at = sa.func.now()
    db.commit()
    db.refresh(incident)
    publish_incident_resolved(incident_id=incident.id)
    duration = incident.resolved_at - incident.started_at
    for user in _list_active_subscribers(db):
        notify_incident_resolved(
            db, account_id=user.account_id, account_email=user.email, incident_reference=incident.id,
            duration_summary=f"{int(duration.total_seconds() // 60)} minutes",
        )
    return incident


def list_incidents(db: Session, *, limit: int = 50) -> list[Incident]:
    """Public, unauthenticated - backs the status page's incident history,
    same posture as get_public_status."""
    return db.query(Incident).order_by(Incident.started_at.desc()).limit(limit).all()


def subscribe_to_status(db: Session, account_id: str, account_email: str) -> StatusSubscription:
    sub = db.query(StatusSubscription).filter(StatusSubscription.account_id == account_id).first()
    if sub is None:
        sub = StatusSubscription(account_id=account_id)
        db.add(sub)
    else:
        sub.is_active = True
    db.commit()
    db.refresh(sub)
    notify_status_subscription_confirmed(db, account_id=account_id, account_email=account_email)
    return sub


def unsubscribe_from_status(db: Session, account_id: str) -> None:
    sub = db.query(StatusSubscription).filter(StatusSubscription.account_id == account_id).first()
    if sub is not None:
        sub.is_active = False
        db.commit()


def get_status_subscription(db: Session, account_id: str) -> StatusSubscription | None:
    return db.query(StatusSubscription).filter(StatusSubscription.account_id == account_id).first()


class KillSwitchTrippedError(Exception):
    """Raised by assert_kill_switch_not_active - a staff member has
    manually halted new activity in this scope (Commercial Billing
    Operating Standard doc §32.1)."""


def list_kill_switches(db: Session) -> list[PlatformKillSwitch]:
    return db.query(PlatformKillSwitch).order_by(PlatformKillSwitch.scope.asc()).all()


def set_kill_switch(
    db: Session, scope: KillSwitchScope, is_active: bool, *, actor: str, reason: str | None = None,
    expires_at: datetime | None = None,
) -> PlatformKillSwitch:
    """Upserts the one row for this scope - see PlatformKillSwitch's
    docstring for why this is an upsert, not an appended history row.
    expires_at is optional (doc §U2 requires the override be time-bounded,
    but not every real incident has a known resolution ETA at activation
    time) - only meaningful when is_active=True; ignored/cleared on
    deactivation."""
    switch = db.query(PlatformKillSwitch).filter(PlatformKillSwitch.scope == scope).first()
    now = datetime.now(timezone.utc)
    if switch is None:
        switch = PlatformKillSwitch(scope=scope)
        db.add(switch)
    switch.is_active = is_active
    switch.reason = reason
    if is_active:
        switch.activated_by = actor
        switch.activated_at = now
        switch.deactivated_at = None
        switch.expires_at = expires_at
    else:
        switch.deactivated_at = now
        switch.expires_at = None
    db.commit()
    db.refresh(switch)
    log_event(
        db, actor=actor, action="ops.kill_switch_activated" if is_active else "ops.kill_switch_deactivated",
        target=f"kill_switch:{scope.value}", after={"reason": reason},
    )
    publish_kill_switch_changed(scope=scope.value, is_active=is_active, actor=actor)
    return switch


def expire_overdue_kill_switches(db: Session) -> dict[str, int]:
    """Commercial Billing Operating Standard doc §U2's "time-bounded"
    override requirement - a switch nobody remembers to turn off is exactly
    the "override outlives the incident" failure the doc is guarding
    against. assert_kill_switch_not_active/assert_account_kill_switch_not_
    active already treat an overdue switch as inactive immediately (so
    nothing is actually blocked between expiry and this sweep running),
    but the row itself still needs flipping to is_active=False for the
    audit trail and staff dashboards to reflect reality without a human
    remembering to do it. Meant to run periodically (same
    app.ops.scheduled_reconciliation daily slot as the other sweeps in
    that script), not on every request."""
    from app.events.service import publish_account_kill_switch_changed
    from app.risk.models import AccountKillSwitch

    now = datetime.now(timezone.utc)
    platform_expired = (
        db.query(PlatformKillSwitch)
        .filter(PlatformKillSwitch.is_active.is_(True), PlatformKillSwitch.expires_at.isnot(None),
                PlatformKillSwitch.expires_at <= now)
        .all()
    )
    for switch in platform_expired:
        switch.is_active = False
        switch.deactivated_at = now
        log_event(
            db, actor="system:kill_switch_expiry", action="ops.kill_switch_deactivated",
            target=f"kill_switch:{switch.scope.value}", after={"reason": "expired", "expires_at": switch.expires_at.isoformat()},
        )
        publish_kill_switch_changed(scope=switch.scope.value, is_active=False, actor="system:kill_switch_expiry")

    account_expired = (
        db.query(AccountKillSwitch)
        .filter(AccountKillSwitch.is_active.is_(True), AccountKillSwitch.expires_at.isnot(None),
                AccountKillSwitch.expires_at <= now)
        .all()
    )
    for switch in account_expired:
        switch.is_active = False
        switch.deactivated_at = now
        log_event(
            db, actor="system:kill_switch_expiry", action="risk.account_kill_switch_deactivated",
            target=f"account_kill_switch:{switch.id}",
            after={"reason": "expired", "account_id": switch.account_id, "expires_at": switch.expires_at.isoformat()},
        )
        # The manual deactivation path (risk/service.py:592) publishes this
        # same event - this automatic expiry sweep was missing it entirely,
        # the same asymmetry as the platform-level branch above already
        # avoids by calling publish_kill_switch_changed on expiry too.
        publish_account_kill_switch_changed(
            switch.account_id, scope=switch.scope.value, is_active=False, actor="system:kill_switch_expiry",
        )

    if platform_expired or account_expired:
        db.commit()
    return {"platform": len(platform_expired), "account": len(account_expired)}


def assert_kill_switch_not_active(db: Session, scope: KillSwitchScope) -> None:
    """Call at the start of any action this scope is meant to halt (number
    provisioning, outbound calling, AI processing, payments/billing) -
    raises before any side effect if a staff member has tripped it.

    A switch past its own expires_at is treated as inactive immediately
    here, not just once expire_overdue_kill_switches's daily sweep catches
    up - doc §U2's "time-bounded" requirement means the override itself
    expires, not just its eventual bookkeeping."""
    switch = db.query(PlatformKillSwitch).filter(PlatformKillSwitch.scope == scope).first()
    if switch is None or not switch.is_active:
        return
    if switch.expires_at is not None and switch.expires_at <= datetime.now(timezone.utc):
        return
    raise KillSwitchTrippedError(
        f"{scope.value} is currently halted by an active kill switch"
        + (f": {switch.reason}" if switch.reason else "")
    )
