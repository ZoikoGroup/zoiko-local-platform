"""
Provider Gateway for Cohere's embeddings API (embeddings category). Only
file allowed to call this vendor's API directly. Powers real semantic
search over AI summaries - see app/intelligence/service.py.
"""

import httpx

from app.core.config import settings

_EMBED_URL = "https://api.cohere.com/v2/embed"
_MODEL = "embed-v4.0"
MODEL_VERSION = f"cohere/{_MODEL}"
EMBEDDING_DIMENSIONS = 1536


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

    try:
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
    except httpx.HTTPError as e:
        raise EmbeddingError(f"Cohere embedding request failed: {e}") from e

    return response.json()["embeddings"]["float"][0]
