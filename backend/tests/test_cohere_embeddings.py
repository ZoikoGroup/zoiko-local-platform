import httpx
import pytest

from app.integrations.embeddings import cohere


def _response(status_code: int, *, json_body: dict, headers: dict | None = None) -> httpx.Response:
    request = httpx.Request("POST", cohere._EMBED_URL)
    return httpx.Response(status_code, json=json_body, headers=headers or {}, request=request)


@pytest.fixture(autouse=True)
def _configure_api_key(monkeypatch):
    monkeypatch.setattr(cohere.settings, "cohere_api_key", "test-key")


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    # The retry path sleeps for real (capped at 2s) - stub it so this test
    # doesn't actually pause, while still letting us assert it was called.
    monkeypatch.setattr(cohere.time, "sleep", lambda seconds: None)


def test_generate_embedding_retries_once_after_a_transient_429(monkeypatch):
    """A single 429 followed by a real 200 - the genuinely transient case
    (e.g. two requests landing in the same rate-limit window) - must
    succeed rather than surfacing an error on the first blip."""
    calls = []

    def _fake_post(url, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            return _response(429, json_body={"message": "rate limited"}, headers={"Retry-After": "0"})
        return _response(200, json_body={"embeddings": {"float": [[0.1, 0.2, 0.3]]}})

    monkeypatch.setattr(cohere.httpx, "post", _fake_post)

    result = cohere.generate_embedding("hello", input_type="search_document")

    assert result == [0.1, 0.2, 0.3]
    assert len(calls) == 2  # one failed attempt, one successful retry


def test_generate_embedding_raises_after_repeated_429s(monkeypatch):
    """A sustained rate-limit/quota exhaustion (every attempt 429s) must
    still surface as a real failure, not be silently papered over - the
    retry is for transient blips, not a way to hide a genuine outage."""
    calls = []

    def _fake_post(url, **kwargs):
        calls.append(url)
        return _response(429, json_body={"message": "rate limited"}, headers={"Retry-After": "0"})

    monkeypatch.setattr(cohere.httpx, "post", _fake_post)

    with pytest.raises(cohere.EmbeddingError):
        cohere.generate_embedding("hello", input_type="search_document")

    assert len(calls) == cohere._MAX_RETRY_ATTEMPTS + 1


def test_generate_embedding_does_not_retry_a_non_429_error(monkeypatch):
    """A 500 (or any non-429 failure) fails immediately - the retry exists
    specifically for rate-limit blips, not general provider flakiness."""
    calls = []

    def _fake_post(url, **kwargs):
        calls.append(url)
        return _response(500, json_body={"message": "internal error"})

    monkeypatch.setattr(cohere.httpx, "post", _fake_post)

    with pytest.raises(cohere.EmbeddingError):
        cohere.generate_embedding("hello", input_type="search_document")

    assert len(calls) == 1
