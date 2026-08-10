import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.integrations.telecom import telnyx
from app.integrations.telecom.twilio import TelecomError


class _FakeResponse:
    def __init__(self, json_data: dict, content: bytes = b""):
        self._json = json_data
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._json


def _configure(monkeypatch, **overrides):
    monkeypatch.setattr(telnyx.settings, "telnyx_enabled", True)
    monkeypatch.setattr(telnyx.settings, "telnyx_api_key", "test-key")
    monkeypatch.setattr(telnyx.settings, "telnyx_connection_id", "conn_123")
    monkeypatch.setattr(telnyx.settings, "telnyx_messaging_profile_id", "profile_123")
    for key, value in overrides.items():
        monkeypatch.setattr(telnyx.settings, key, value)


# --- not configured ---

def test_health_check_reports_disabled_by_default():
    result = telnyx.health_check()
    assert result["configured"] is False
    assert result["ok"] is False


def test_send_sms_requires_api_key(monkeypatch):
    monkeypatch.setattr(telnyx.settings, "telnyx_api_key", "")
    with pytest.raises(TelecomError, match="not configured"):
        telnyx.send_sms("+15551234567", "hi")


def test_send_sms_requires_messaging_profile(monkeypatch):
    _configure(monkeypatch, telnyx_messaging_profile_id="")
    with pytest.raises(TelecomError, match="TELNYX_MESSAGING_PROFILE_ID"):
        telnyx.send_sms("+15551234567", "hi")


def test_buy_number_requires_connection_id(monkeypatch):
    _configure(monkeypatch, telnyx_connection_id="")
    with pytest.raises(TelecomError, match="TELNYX_CONNECTION_ID"):
        telnyx.buy_number("+15551234567")


def test_place_call_requires_connection_id(monkeypatch):
    _configure(monkeypatch, telnyx_connection_id="")
    with pytest.raises(TelecomError, match="TELNYX_CONNECTION_ID"):
        telnyx.place_call("+15551234567", "+15557654321")


# --- request shape + response parsing ---

def test_send_sms_builds_correct_request_and_parses_response(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse({"data": {"id": "msg_abc", "to": [{"status": "queued"}]}})

    monkeypatch.setattr(telnyx.httpx, "post", _fake_post)

    result = telnyx.send_sms("+15551234567", "hello there")
    assert captured["url"] == "https://api.telnyx.com/v2/messages"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["to"] == "+15551234567"
    assert captured["json"]["text"] == "hello there"
    assert captured["json"]["messaging_profile_id"] == "profile_123"
    assert result == {"sid": "msg_abc", "status": "queued"}


def test_search_available_numbers_builds_correct_filters(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_get(url, headers=None, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse({"data": [{"phone_number": "+15550001111", "features": ["voice", "sms"]}]})

    monkeypatch.setattr(telnyx.httpx, "get", _fake_get)

    result = telnyx.search_available_numbers("US", "local", area_code="415", contains="123", limit=5)
    assert captured["params"]["filter[country_code]"] == "US"
    assert captured["params"]["filter[phone_number_type]"] == "local"
    assert captured["params"]["filter[national_destination_code]"] == "415"
    assert captured["params"]["filter[phone_number][contains]"] == "123"
    assert result == [
        {
            "phone_number": "+15550001111", "locality": None, "region": None,
            "capabilities": {"voice": True, "sms": True}, "address_requirements": "none",
        }
    ]


def test_list_owned_numbers_parses_response(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        telnyx.httpx, "get",
        lambda url, headers=None, timeout=None: _FakeResponse(
            {"data": [{"id": "num_1", "phone_number": "+15550001111", "features": ["voice"]}]}
        ),
    )
    result = telnyx.list_owned_numbers()
    assert result == [{"sid": "num_1", "phone_number": "+15550001111", "capabilities": {"voice": True}}]


def test_buy_number_builds_correct_request(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"data": {"phone_numbers": [{"id": "num_9", "phone_number": "+15550001111"}]}})

    monkeypatch.setattr(telnyx.httpx, "post", _fake_post)

    result = telnyx.buy_number("+15550001111")
    assert captured["url"] == "https://api.telnyx.com/v2/number_orders"
    assert captured["json"]["phone_numbers"] == [{"phone_number": "+15550001111"}]
    assert captured["json"]["connection_id"] == "conn_123"
    assert result == {"sid": "num_9", "phone_number": "+15550001111", "capabilities": {}}


def test_set_voice_webhook_assigns_connection(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_patch(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"data": {}})

    monkeypatch.setattr(telnyx.httpx, "patch", _fake_patch)
    telnyx.set_voice_webhook("num_9", "https://example.com")
    assert captured["url"] == "https://api.telnyx.com/v2/phone_numbers/num_9"
    assert captured["json"] == {"connection_id": "conn_123"}


def test_release_number_sends_delete(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_delete(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse({})

    monkeypatch.setattr(telnyx.httpx, "delete", _fake_delete)
    telnyx.release_number("num_9")
    assert captured["url"] == "https://api.telnyx.com/v2/phone_numbers/num_9"


def test_place_call_rejects_twiml(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(TelecomError, match="no TwiML equivalent"):
        telnyx.place_call("+15551234567", "+15557654321", twiml="<Response><Say>hi</Say></Response>")


def test_place_call_builds_correct_request(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResponse({"data": {"call_control_id": "cc_1"}})

    monkeypatch.setattr(telnyx.httpx, "post", _fake_post)
    result = telnyx.place_call("+15551234567", "+15557654321", status_callback_url="https://example.com/hook")
    assert captured["json"]["connection_id"] == "conn_123"
    assert captured["json"]["to"] == "+15551234567"
    assert captured["json"]["from"] == "+15557654321"
    assert captured["json"]["webhook_url"] == "https://example.com/hook"
    assert result == {"sid": "cc_1", "status": "initiated", "to": "+15551234567", "from": "+15557654321"}


def test_get_call_raises_architectural_gap(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(TelecomError, match="webhook"):
        telnyx.get_call("cc_1")


def test_list_calls_raises_architectural_gap(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(TelecomError, match="webhook"):
        telnyx.list_calls()


def test_download_recording_fetches_url_directly(monkeypatch):
    def _fake_get(url, timeout=None):
        assert url == "https://recordings.telnyx.com/abc.mp3"
        return _FakeResponse({}, content=b"audio-bytes")

    monkeypatch.setattr(telnyx.httpx, "get", _fake_get)
    result = telnyx.download_recording("https://recordings.telnyx.com/abc.mp3")
    assert result == b"audio-bytes"


def test_delete_recording_sends_delete(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def _fake_delete(url, headers=None, timeout=None):
        captured["url"] = url
        return _FakeResponse({})

    monkeypatch.setattr(telnyx.httpx, "delete", _fake_delete)
    telnyx.delete_recording("rec_1")
    assert captured["url"] == "https://api.telnyx.com/v2/recordings/rec_1"


# --- webhook signature verification (real Ed25519 crypto, no live account needed) ---

def _raw_public_key_b64(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def test_validate_webhook_signature_accepts_a_correctly_signed_payload(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(telnyx.settings, "telnyx_public_key", _raw_public_key_b64(private_key))

    payload = b'{"event_type": "call.initiated"}'
    timestamp = "1700000000"
    signature = base64.b64encode(private_key.sign(timestamp.encode() + b"|" + payload)).decode()

    assert telnyx.validate_webhook_signature(payload, signature, timestamp) is True


def test_validate_webhook_signature_rejects_a_tampered_payload(monkeypatch):
    private_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(telnyx.settings, "telnyx_public_key", _raw_public_key_b64(private_key))

    timestamp = "1700000000"
    signature = base64.b64encode(private_key.sign(timestamp.encode() + b"|" + b"original")).decode()

    assert telnyx.validate_webhook_signature(b"tampered", signature, timestamp) is False


def test_validate_webhook_signature_false_when_unconfigured():
    assert telnyx.validate_webhook_signature(b"payload", "sig", "123") is False


def test_validate_webhook_signature_false_when_signature_missing(monkeypatch):
    monkeypatch.setattr(telnyx.settings, "telnyx_public_key", "not-empty")
    assert telnyx.validate_webhook_signature(b"payload", None, "123") is False
