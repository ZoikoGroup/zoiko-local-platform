"""Generic, vendor-agnostic circuit breaker + primary/secondary failover
helper, shared by every integrations/<category>/ Provider Gateway.

This module imports no vendor SDK, so it doesn't violate CLAUDE.md's
Provider Gateway rule ("only files inside integrations/<category>/ import a
vendor SDK directly") - it's plain failover plumbing any category can reuse,
the same way app.audit.service.log_event() is reused by every domain
service rather than reimplemented per module.
"""

import logging
import threading
import time
from enum import Enum
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger("zoiko.failover")

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Per-category, in-memory, per-process breaker - not shared across
    processes/replicas (no Redis/DB backing yet, consistent with this
    codebase's current single-process maturity level)."""

    def __init__(self, name: str, *, failure_threshold: int = 3, reset_timeout_seconds: float = 30.0):
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_timeout_seconds = reset_timeout_seconds
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN and self._opened_at is not None:
                if time.monotonic() - self._opened_at >= self.reset_timeout_seconds:
                    # Half-open: let the next call try the primary again
                    # rather than staying open forever with nothing to
                    # signal recovery.
                    self._state = CircuitState.HALF_OPEN
            return self._state

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()


def with_failover(
    breaker: CircuitBreaker,
    primary: Callable[[], T],
    secondary: Callable[[], T] | None,
    expected_exception: type[Exception],
) -> T:
    """Call primary unless the breaker is already open; on expected_exception
    from primary, record the failure and fall back to secondary if one is
    configured - otherwise re-raise the original error. A HALF_OPEN breaker
    is treated like CLOSED here: exactly the trial call that lets the
    breaker recover if the primary is healthy again."""
    if breaker.state != CircuitState.OPEN:
        try:
            result = primary()
            breaker.record_success()
            return result
        except expected_exception as e:
            breaker.record_failure()
            if secondary is None:
                raise
            logger.warning("%s: primary provider failed, falling back to secondary: %s", breaker.name, e)
            return secondary()

    if secondary is None:
        raise expected_exception(f"{breaker.name}: circuit open and no secondary provider configured")
    logger.warning("%s: circuit open, calling secondary directly", breaker.name)
    return secondary()


async def with_failover_async(
    breaker: CircuitBreaker,
    primary: Callable[[], Awaitable[T]],
    secondary: Callable[[], Awaitable[T]] | None,
    expected_exception: type[Exception],
) -> T:
    """Async counterpart to with_failover, for Provider Gateways whose SDK
    calls are async (e.g. LiveKit) - same CLOSED/OPEN/HALF_OPEN semantics."""
    if breaker.state != CircuitState.OPEN:
        try:
            result = await primary()
            breaker.record_success()
            return result
        except expected_exception as e:
            breaker.record_failure()
            if secondary is None:
                raise
            logger.warning("%s: primary provider failed, falling back to secondary: %s", breaker.name, e)
            return await secondary()

    if secondary is None:
        raise expected_exception(f"{breaker.name}: circuit open and no secondary provider configured")
    logger.warning("%s: circuit open, calling secondary directly", breaker.name)
    return await secondary()
