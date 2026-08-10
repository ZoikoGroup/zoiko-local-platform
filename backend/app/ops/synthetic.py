"""Periodic synthetic check - re-runs the same provider health_check() sweep
that backs /ops/provider-status on a timer, logging the result (structured,
via core/logging.py) and emitting an OTel up/down gauge per provider so a
dashboard can plot reachability over time instead of only-on-request.
Disabled by default (SYNTHETIC_CHECK_INTERVAL_SECONDS=0), matching this
codebase's "blank/0/false disables" pattern for anything requiring no real
backend to exist. Run standalone:

    python -m app.ops.synthetic
"""

import asyncio
import logging
import time

from opentelemetry import metrics

from app.core.config import settings
from app.ops.service import get_provider_statuses

logger = logging.getLogger("zoiko.ops")

_meter = metrics.get_meter("zoiko.ops.synthetic")
_provider_up = _meter.create_gauge(
    "zoiko.provider.up", description="1 if a provider's health_check() succeeded, 0 otherwise"
)


async def run_once() -> list[dict]:
    statuses = await get_provider_statuses()
    for status in statuses:
        healthy = bool(status["configured"] and status["ok"])
        _provider_up.set(1 if healthy else 0, attributes={"provider": status["name"]})
        logger.info(
            "synthetic check: provider=%s configured=%s ok=%s detail=%s",
            status["name"], status["configured"], status["ok"], status.get("detail"),
        )
    return statuses


async def run() -> None:
    if not settings.synthetic_check_interval_seconds:
        logger.info("Synthetic checks disabled (SYNTHETIC_CHECK_INTERVAL_SECONDS=0)")
        return

    logger.info("Synthetic check worker starting - interval=%ss", settings.synthetic_check_interval_seconds)
    while True:
        start = time.monotonic()
        try:
            await run_once()
        except Exception:
            logger.exception("Synthetic check sweep failed")
        elapsed = time.monotonic() - start
        await asyncio.sleep(max(0.0, settings.synthetic_check_interval_seconds - elapsed))


if __name__ == "__main__":
    asyncio.run(run())
