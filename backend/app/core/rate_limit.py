"""
Security-review fix: neither /auth/login nor /staff/login had any limit on
failed attempts - unlimited password guessing was possible against any
account. Storage is Redis-backed when settings.redis_url is set (see
docker-compose.yml's `redis` service) so every uvicorn worker - and every
instance, if this ever runs behind a load balancer - shares one counter.
Falls back to slowapi's default in-memory storage when redis_url is blank
(e.g. running app.main directly without docker-compose) - correct only for
a single worker process, since in-memory storage is per-process.

in_memory_fallback_enabled/swallow_errors: confirmed live as a real gap -
unlike every other Redis touchpoint in this codebase (integrations/cache/
redis.py's circuit breaker, the Kafka publisher's fire-and-forget design),
this Limiter had zero resilience to a Redis outage: a slow/unreachable
Redis made every rate-limited route (login, signup, MFA, video, public
API) raise an unhandled connection error instead of degrading, exactly the
class of "an outage in a supporting system must never break a request that
would've worked fine otherwise" bug those other two already guard against.
in_memory_fallback_enabled makes slowapi itself detect a dead storage
backend and transparently fail over to a real (if degraded - per-worker,
not shared) in-memory limiter instead, self-healing once Redis recovers
(see slowapi.Extension._check_request_limit) - strictly better than fully
disabling rate limiting, while still never blocking the request the way
the bare Redis-backed limiter did. swallow_errors is a defensive backstop
for the fallback path itself ever raising."""

from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address, storage_uri=settings.redis_url or "memory://",
    in_memory_fallback_enabled=True, swallow_errors=True,
)


def api_key_or_ip(request: Request) -> str:
    """Real gap fix: every /public/v1/* route used the limiter's default
    IP-based key, but those routes authenticate via a long-lived API key
    (app.core.deps.get_api_key_account_id), not a browser session bound to
    one IP - a leaked/stolen key can be replayed from many source IPs to
    blow past its rate limit entirely, since the limit tracked the caller's
    IP, not the authenticated account. The reverse is also real: unrelated
    tenants whose traffic egresses through the same IP (corporate NAT, a
    shared proxy) shared one bucket, so one account's usage could throttle
    another's legitimate calls. get_api_key_account_id already sets
    request.state.account_id once the key is verified, and - confirmed by
    direct testing - FastAPI resolves that dependency before slowapi's
    key_func runs for the same route (the key_func's Request parameter
    already reflects it), so keying on it here needs no restructuring of
    the routes themselves, just this key_func passed into each @limiter.
    limit(...) call in public_api/routes.py. Falls back to IP only for the
    handful of callers that reach this before authentication succeeds
    (an invalid/missing key never sets account_id, so those attempts still
    get limited by IP rather than being unbounded)."""
    return getattr(request.state, "account_id", None) or get_remote_address(request)
