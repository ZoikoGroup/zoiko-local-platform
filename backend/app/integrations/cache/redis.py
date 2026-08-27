"""
Provider Gateway for Redis (cache category) - the only file allowed to
import the redis client directly. Cross-cutting infra shared by every
domain rather than a per-vendor swap candidate, same shape as
integrations/eventbus/kafka.py - reuses the same REDIS_URL already wired
for app.core.rate_limit's shared rate-limit counters (see docker-compose.yml's
`redis` service), just for a different purpose (caching read-heavy,
slow-to-recompute data instead of counting requests).

Best-effort, same "never fails or blocks the underlying operation"
philosophy as the Kafka publisher: a Redis outage - or REDIS_URL simply
left blank, e.g. Render's free-plan deploy (see render.yaml) - must never
break a request that would have worked fine without caching. Every
function here degrades to "no cache" (get returns None, set/delete are
no-ops) rather than raising; callers always still have the real
database/service call as the source of truth on a cache miss.

Circuit-broken like every other Provider Gateway in this codebase (see
integrations/_shared/circuit_breaker.py) - confirmed live that a
plain per-call socket timeout alone isn't enough here: when nothing is
listening on the configured host:port at all (Redis down, or REDIS_URL
pointed at a stale address), a single redis-py call can take several
seconds to time out on this stack, and this cache is consulted on nearly
every authenticated request (see app.core.deps). Without a breaker, an
actually-down Redis would make every request SLOWER than having no cache
at all, defeating the entire point. Once open, callers get an instant
"no cache" for reset_timeout_seconds instead of paying that cost on every
single request.
"""

import json
import logging

import redis

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, CircuitState

logger = logging.getLogger("zoiko.cache")

_client: "redis.Redis | None" = None
_client_initialized = False

# Lower failure_threshold/reset_timeout than other Provider Gateways in
# this codebase (default 3/30s) - there's zero correctness cost to tripping
# the breaker eagerly here (worst case: a few extra cache misses), unlike
# email/telecom where a false trip means failing over to a real secondary
# vendor. Short socket timeouts below are the first line of defense; the
# breaker is what keeps repeated failures cheap after that.
_breaker = CircuitBreaker("redis_cache", failure_threshold=2, reset_timeout_seconds=20.0)


def _get_client() -> "redis.Redis | None":
    global _client, _client_initialized
    if not _client_initialized:
        _client_initialized = True
        if settings.redis_url:
            _client = redis.Redis.from_url(
                settings.redis_url, socket_timeout=0.3, socket_connect_timeout=0.3,
            )
    return _client


def _run(op_name: str, key, fn) -> bool:
    """Runs fn() against the client if the breaker allows it, recording
    success/failure. Returns whether fn() actually ran - callers use this
    to tell "breaker open / no client configured" apart from "ran and
    failed" for logging, though both end up as a no-op/miss either way."""
    client = _get_client()
    if client is None or _breaker.state == CircuitState.OPEN:
        return False
    try:
        fn(client)
        _breaker.record_success()
        return True
    except redis.RedisError as e:
        _breaker.record_failure()
        logger.warning("%s(%s) failed: %s", op_name, key, e)
        return False


def cache_get(key: str) -> dict | list | None:
    """Returns the cached value, or None on a miss, a Redis outage, an
    open breaker, or when no REDIS_URL is configured - all look identical
    to a caller, which always has a real fallback for exactly this
    reason."""
    result: list = [None]

    def _op(client):
        result[0] = client.get(key)

    if not _run("cache_get", key, _op) or result[0] is None:
        return None
    try:
        return json.loads(result[0])
    except ValueError:
        return None


def cache_set(key: str, value: dict | list, *, ttl_seconds: int) -> None:
    _run("cache_set", key, lambda client: client.set(key, json.dumps(value), ex=ttl_seconds))


def cache_delete(*keys: str) -> None:
    """Best-effort invalidation for a write that just made a cached read
    stale. A failed delete just means that key rides out its own TTL
    instead of being evicted early - not a correctness problem, since
    every cached value here already has a bounded TTL."""
    if not keys:
        return
    _run("cache_delete", keys, lambda client: client.delete(*keys))


def flush_all_for_tests() -> None:
    """Test-only: wipes the whole configured Redis DB. Every other cache
    call site here degrades silently to "no cache" when Redis isn't
    configured, which is exactly why this cache was invisible to the test
    suite until REDIS_URL pointed at a real instance - tests that mutate a
    row a cache_get/cache_set pair reads (a Plan's max_team_seats, the
    supported-countries list, ops' public status) now need this run
    between tests, same reasoning as this file's own reset_rate_limiter/
    reset_circuit_breakers fixtures for other shared, process-external
    state. Not exposed for anything outside tests/conftest.py."""
    _run("flush_all_for_tests", "*", lambda client: client.flushdb())
