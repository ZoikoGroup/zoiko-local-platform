"""Tests for the shared circuit-breaker/failover plumbing
(app.integrations._shared.circuit_breaker) plus representative wiring checks
proving a few real Provider Gateways actually route through it - not every
one of the 8 wrapped categories, since they all share the identical
mechanical pattern already proven generically below."""

import httpx
import pytest

from app.integrations._shared.circuit_breaker import CircuitBreaker, CircuitState, with_failover, with_failover_async


class _Boom(Exception):
    pass


# --- CircuitBreaker state machine ---

def test_circuit_breaker_starts_closed():
    breaker = CircuitBreaker("test")
    assert breaker.state == CircuitState.CLOSED


def test_circuit_breaker_opens_after_failure_threshold():
    breaker = CircuitBreaker("test", failure_threshold=3)
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == CircuitState.OPEN


def test_circuit_breaker_stays_closed_below_threshold():
    breaker = CircuitBreaker("test", failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED


def test_circuit_breaker_resets_on_success():
    breaker = CircuitBreaker("test", failure_threshold=3)
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == CircuitState.CLOSED  # only 2 failures since the reset


def test_circuit_breaker_transitions_to_half_open_after_reset_timeout(monkeypatch):
    breaker = CircuitBreaker("test", failure_threshold=1, reset_timeout_seconds=0.05)
    breaker.record_failure()
    assert breaker.state == CircuitState.OPEN

    import time
    time.sleep(0.1)
    assert breaker.state == CircuitState.HALF_OPEN


# --- with_failover routing ---

def test_with_failover_returns_primary_result_on_success():
    breaker = CircuitBreaker("test")
    result = with_failover(breaker, lambda: "primary-ok", None, _Boom)
    assert result == "primary-ok"
    assert breaker.state == CircuitState.CLOSED


def test_with_failover_falls_back_to_secondary_on_primary_failure():
    breaker = CircuitBreaker("test", failure_threshold=3)

    def _primary():
        raise _Boom("primary down")

    result = with_failover(breaker, _primary, lambda: "secondary-ok", _Boom)
    assert result == "secondary-ok"
    assert breaker.state == CircuitState.CLOSED  # one failure, below threshold=3


def test_with_failover_reraises_when_no_secondary_configured():
    breaker = CircuitBreaker("test")

    def _primary():
        raise _Boom("primary down")

    with pytest.raises(_Boom):
        with_failover(breaker, _primary, None, _Boom)


def test_with_failover_calls_secondary_directly_once_circuit_is_open():
    breaker = CircuitBreaker("test", failure_threshold=1)
    breaker.record_failure()  # opens the breaker
    assert breaker.state == CircuitState.OPEN

    primary_calls = []

    def _primary():
        primary_calls.append(1)
        return "should not be called"

    result = with_failover(breaker, _primary, lambda: "secondary-ok", _Boom)
    assert result == "secondary-ok"
    assert primary_calls == []  # primary never invoked while circuit is open


def test_with_failover_open_circuit_with_no_secondary_raises_clear_error():
    breaker = CircuitBreaker("test", failure_threshold=1)
    breaker.record_failure()

    with pytest.raises(_Boom, match="circuit open and no secondary provider configured"):
        with_failover(breaker, lambda: "unreachable", None, _Boom)


def test_with_failover_async_falls_back_to_secondary_on_primary_failure():
    # No pytest-asyncio in this project's dependencies - run the coroutine
    # directly, same as this codebase's own async LiveKit calls are driven
    # by FastAPI's event loop rather than a pytest-asyncio marker.
    import asyncio

    breaker = CircuitBreaker("test", failure_threshold=3)

    async def _primary():
        raise _Boom("primary down")

    async def _secondary():
        return "secondary-ok"

    async def _run():
        return await with_failover_async(breaker, _primary, _secondary, _Boom)

    assert asyncio.run(_run()) == "secondary-ok"


# --- Representative real-integration wiring checks ---

def test_telecom_send_sms_falls_back_to_secondary_stub_when_enabled(monkeypatch):
    from app.integrations.telecom import twilio
    from twilio.base.exceptions import TwilioException

    monkeypatch.setattr(twilio.settings, "twilio_trial_number", "+15550001111")
    monkeypatch.setattr(twilio.settings, "telecom_failover_enabled", True)
    monkeypatch.setattr(twilio, "_breaker", type(twilio._breaker)("telecom-test"))
    # Explicitly blank, not relying on .env having no VONAGE_* set - real
    # Vonage credentials were added to .env this session (live-tested
    # search/purchase against a real account), so this test would
    # otherwise silently depend on real environment state to reach the
    # exact "not configured" branch it's testing.
    monkeypatch.setattr(twilio.secondary.settings, "vonage_api_key", "")
    monkeypatch.setattr(twilio.secondary.settings, "vonage_api_secret", "")

    def _raise_client():
        raise TwilioException("simulated outage")

    monkeypatch.setattr(twilio, "_client", _raise_client)

    with pytest.raises(twilio.TelecomError, match="Secondary telecom provider .* is not configured"):
        twilio.send_sms("+15551234567", "hello")


def test_telecom_send_sms_falls_back_when_trial_number_is_unset(monkeypatch):
    """A blank TWILIO_TRIAL_NUMBER is a primary-provider failure like any
    other - it must go through with_failover (and be rescuable by the
    secondary) rather than raising before with_failover ever runs, which
    silently defeated TELECOM_FAILOVER_ENABLED for exactly this case."""
    from app.integrations.telecom import twilio

    monkeypatch.setattr(twilio.settings, "twilio_trial_number", "")
    monkeypatch.setattr(twilio.settings, "telecom_failover_enabled", True)
    monkeypatch.setattr(twilio, "_breaker", type(twilio._breaker)("telecom-test"))

    called = {}

    def _secondary_send_sms(to, body):
        called["to"] = to
        called["body"] = body
        return {"sid": "secondary-sid", "status": "queued"}

    monkeypatch.setattr(twilio.secondary, "send_sms", _secondary_send_sms)

    result = twilio.send_sms("+15551234567", "hello")

    assert result == {"sid": "secondary-sid", "status": "queued"}
    assert called == {"to": "+15551234567", "body": "hello"}


def test_telecom_send_sms_reraises_original_error_when_failover_disabled(monkeypatch):
    from app.integrations.telecom import twilio
    from twilio.base.exceptions import TwilioException

    monkeypatch.setattr(twilio.settings, "twilio_trial_number", "+15550001111")
    monkeypatch.setattr(twilio.settings, "telecom_failover_enabled", False)
    monkeypatch.setattr(twilio, "_breaker", type(twilio._breaker)("telecom-test"))

    def _raise_client():
        raise TwilioException("simulated outage")

    monkeypatch.setattr(twilio, "_client", _raise_client)

    with pytest.raises(twilio.TelecomError, match="simulated outage"):
        twilio.send_sms("+15551234567", "hello")


def test_telecom_circuit_opens_after_repeated_failures(monkeypatch):
    from app.integrations.telecom import twilio
    from app.integrations._shared.circuit_breaker import CircuitState
    from twilio.base.exceptions import TwilioException

    monkeypatch.setattr(twilio.settings, "twilio_trial_number", "+15550001111")
    monkeypatch.setattr(twilio.settings, "telecom_failover_enabled", False)
    test_breaker = CircuitBreaker("telecom-test", failure_threshold=3)
    monkeypatch.setattr(twilio, "_breaker", test_breaker)
    monkeypatch.setattr(twilio, "_client", lambda: (_ for _ in ()).throw(TwilioException("down")))

    for _ in range(3):
        with pytest.raises(twilio.TelecomError):
            twilio.send_sms("+15551234567", "hello")

    assert test_breaker.state == CircuitState.OPEN


def test_llm_extract_conversation_summary_falls_back_to_secondary_stub_when_enabled(monkeypatch):
    from app.integrations.llm import groq

    monkeypatch.setattr(groq.settings, "groq_api_key", "fake-key")
    monkeypatch.setattr(groq.settings, "llm_failover_enabled", True)
    monkeypatch.setattr(groq, "_breaker", CircuitBreaker("llm-test"))

    def _raise_post(*args, **kwargs):
        raise httpx.ConnectError("simulated network failure")

    monkeypatch.setattr(groq.httpx, "post", _raise_post)

    with pytest.raises(groq.LLMError, match="Secondary LLM provider .* is not configured"):
        groq.extract_conversation_summary("hello world")


def test_storage_delete_object_reraises_when_not_configured_and_no_secondary(monkeypatch):
    from app.integrations.storage import s3

    monkeypatch.setattr(s3.settings, "s3_bucket", "")
    monkeypatch.setattr(s3.settings, "storage_failover_enabled", False)
    monkeypatch.setattr(s3, "_breaker", CircuitBreaker("storage-test"))

    with pytest.raises(s3.StorageError, match="not configured"):
        s3.delete_object("some/key.mp4")


def test_storage_delete_download_presign_never_fail_over_even_when_enabled(monkeypatch):
    """Real gap fix: upload_object only ever writes to the PRIMARY bucket -
    objects are never replicated to the secondary. delete_object/
    download_object/generate_presigned_url used to fail over anyway when
    storage_failover_enabled=True, which could make delete_object "succeed"
    against an empty secondary bucket while the real file survived
    untouched on a temporarily-unreachable primary - a caller like
    retention.service would then wrongly mark the recording as purged.
    Fixed by making these three ops secondary_fn=None unconditionally, same
    as buy_number's deliberate no-failover posture elsewhere in this
    codebase. This proves it holds even with storage_failover_enabled=True
    and even against a genuine 5xx (the exact case with_failover would
    otherwise treat as a real provider-health failure worth falling over)."""
    from botocore.exceptions import ClientError

    from app.integrations.storage import _secondary_stub, s3

    monkeypatch.setattr(s3.settings, "s3_bucket", "test-bucket")
    monkeypatch.setattr(s3.settings, "s3_access_key_id", "fake-key-id")
    monkeypatch.setattr(s3.settings, "s3_secret_access_key", "fake-secret")
    monkeypatch.setattr(s3.settings, "storage_failover_enabled", True)
    monkeypatch.setattr(s3, "_breaker", CircuitBreaker("storage-test"))

    secondary_calls = []
    monkeypatch.setattr(_secondary_stub, "delete_object", lambda key: secondary_calls.append(("delete", key)))
    monkeypatch.setattr(_secondary_stub, "download_object", lambda key: secondary_calls.append(("download", key)))
    monkeypatch.setattr(
        _secondary_stub, "generate_presigned_url",
        lambda key, expires_in=3600: secondary_calls.append(("presign", key)),
    )

    def _boom(*args, **kwargs):
        # A genuine 5xx - is_breaker_failure would classify this as a real
        # provider-health signal, exactly the case that would normally
        # trigger failover if a secondary_fn were configured.
        raise ClientError(
            {"Error": {"Code": "InternalError", "Message": "boom"}, "ResponseMetadata": {"HTTPStatusCode": 500}},
            "DeleteObject",
        )

    class _FakeClient:
        delete_object = staticmethod(_boom)
        get_object = staticmethod(_boom)
        generate_presigned_url = staticmethod(_boom)

    monkeypatch.setattr(s3, "_client", lambda: _FakeClient())

    with pytest.raises(s3.StorageError):
        s3.delete_object("some/key.mp4")
    with pytest.raises(s3.StorageError):
        s3.download_object("some/key.mp4")
    with pytest.raises(s3.StorageError):
        s3.generate_presigned_url("some/key.mp4")

    assert secondary_calls == [], (
        f"the secondary storage stub must never be called for delete/download/presign, since objects "
        f"aren't replicated to it - got: {secondary_calls}"
    )
