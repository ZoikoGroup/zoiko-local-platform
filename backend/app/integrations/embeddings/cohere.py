"""
Provider Gateway for Cohere's embeddings API (embeddings category). Only
file allowed to call this vendor's API directly. Powers real semantic
search over AI summaries - see app/intelligence/service.py.
"""

import time

import httpx

from app.core.config import settings
from app.observability.service import trace_provider_call

_EMBED_URL = "https://api.cohere.com/v2/embed"
_MODEL = "embed-v4.0"
MODEL_VERSION = f"cohere/{_MODEL}"
EMBEDDING_DIMENSIONS = 1536

# A single bounded retry on 429 - enough to ride out a short burst (e.g. two
# requests landing in the same rate-limit window), which is genuinely
# transient. NOT enough to paper over a real daily-quota exhaustion (Cohere's
# free trial tier) - that will still correctly report degraded after the
# retry, same as before. Capped low (2s) since this call is made from
# app.ops.service.get_provider_statuses' synchronous status check without
# being awaited - a long sleep here would block that request's event loop.
_MAX_RETRY_ATTEMPTS = 1
_MAX_BACKOFF_SECONDS = 2.0


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return _MAX_BACKOFF_SECONDS
    try:
        return min(float(raw), _MAX_BACKOFF_SECONDS)
    except ValueError:
        # Retry-After can also be an HTTP-date per RFC 9110 - not worth
        # parsing for a capped 2s backoff either way.
        return _MAX_BACKOFF_SECONDS


class EmbeddingError(Exception):
    """Raised instead of letting an httpx/vendor-specific exception escape this module."""


def health_check() -> dict:
    """Real reachability check - embeds a single short word, the cheapest
    real call this API offers (there's no free/metadata-only endpoint)."""
    if not settings.cohere_api_key:
        return {"configured": False, "ok": False, "detail": None}
    try:
        generate_embedding("ping", input_type="search_document")
        return {"configured": True, "ok": True, "detail": None}
    except EmbeddingError as e:
        return {"configured": True, "ok": False, "detail": str(e)}


def generate_embedding(text: str, *, input_type: str) -> list[float]:
    """input_type must be "search_document" when embedding text being
    stored for later retrieval, or "search_query" when embedding a user's
    search box input - Cohere's v4 models are trained asymmetrically for
    this and mixing them up measurably hurts search quality."""
    if not settings.cohere_api_key:
        raise EmbeddingError("Cohere API key is not configured")

    for attempt in range(_MAX_RETRY_ATTEMPTS + 1):
        try:
            with trace_provider_call("cohere", "generate_embedding"):
                response = httpx.post(
                    _EMBED_URL,
                    headers={"Authorization": f"Bearer {settings.cohere_api_key}"},
                    json={
                        "model": _MODEL,
                        "texts": [text],
                        "input_type": input_type,
                        "embedding_types": ["float"],
                    },
                    timeout=30.0,
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429 and attempt < _MAX_RETRY_ATTEMPTS:
                time.sleep(_retry_after_seconds(e.response))
                continue
            raise EmbeddingError(f"Cohere embedding request failed: {e}") from e
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Cohere embedding request failed: {e}") from e

        return response.json()["embeddings"]["float"][0]

    raise AssertionError("unreachable - loop always returns or raises")
