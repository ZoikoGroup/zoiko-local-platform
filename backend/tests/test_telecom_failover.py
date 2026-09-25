"""Real coverage for the India/other-Twilio-uncovered-country number
search gap: Twilio has zero Local/Mobile/TollFree inventory for some
countries at all (confirmed live against the real account: India returns
a 404 for every type except Mobile), which used to surface as a hard
error with no fallback even though Vonage (the already-configured,
already-tested secondary telecom provider) genuinely can search that same
country. See app.integrations.telecom.twilio.NoCoverageError's docstring
for the full reasoning."""

from twilio.base.exceptions import TwilioException, TwilioRestException

from app.integrations.telecom import twilio as telecom_module
from app.integrations.telecom.twilio import (
    NoCoverageError,
    TelecomError,
    _is_404_response,
    buy_number_via_provider,
    search_available_numbers,
)


def test_is_404_response_detects_a_proper_rest_exception():
    e = TwilioRestException(status=404, uri="/AvailablePhoneNumbers", msg="not found")
    assert _is_404_response(e) is True


def test_is_404_response_detects_the_bare_exception_class_too():
    """Real bug found live: Twilio's SDK raises the BARE TwilioException
    (no .status attribute) from the paginated .list()/.stream()/.page()
    call chain used by search_available_numbers - an isinstance(e,
    TwilioRestException) check alone silently never catches this, even
    though the underlying failure genuinely is a 404."""
    e = TwilioException(
        "Unable to fetch page",
        'HTTP 404 {"code":20404,"message":"The requested resource /2010-04-01/Accounts/AC.../'
        'AvailablePhoneNumbers/IN/Local.json was not found","status":404}',
    )
    assert _is_404_response(e) is True


def test_is_404_response_is_false_for_an_unrelated_error():
    e = TwilioRestException(status=400, uri="/AvailablePhoneNumbers", msg="bad request")
    assert _is_404_response(e) is False


class _FakeResource:
    def __init__(self, error):
        self._error = error

    def list(self, **kwargs):
        raise self._error


class _FakeCountry:
    def __init__(self, error):
        self.local = _FakeResource(error)
        self.mobile = _FakeResource(error)
        self.toll_free = _FakeResource(error)


class _FakeClient:
    def __init__(self, error):
        self._error = error

    def available_phone_numbers(self, country):
        return _FakeCountry(self._error)


def test_search_falls_back_to_vonage_when_twilio_has_no_coverage(monkeypatch):
    monkeypatch.setattr("app.core.config.settings.telecom_failover_enabled", True)
    error = TwilioException("Unable to fetch page", 'HTTP 404 {"status":404}')
    monkeypatch.setattr(telecom_module, "_client", lambda: _FakeClient(error))

    fake_vonage_results = [
        {"phone_number": "+917039068350", "locality": None, "region": "IN", "capabilities": {"SMS": True}, "address_requirements": "none"},
    ]
    monkeypatch.setattr(
        telecom_module.secondary, "search_available_numbers",
        lambda country, **kwargs: fake_vonage_results,
    )

    results = search_available_numbers("IN", number_type="mobile")
    assert len(results) == 1
    assert results[0]["provider"] == "vonage"
    assert results[0]["phone_number"] == "+917039068350"


def test_search_does_not_fall_back_when_failover_is_disabled(monkeypatch):
    """The failover-disabled flag must be respected here the same way it's
    respected at every other secondary-provider call site in this module -
    otherwise a country with genuinely no Twilio coverage would still try
    to call Vonage even when an operator has deliberately turned the
    secondary provider off."""
    monkeypatch.setattr("app.core.config.settings.telecom_failover_enabled", False)
    error = TwilioException("Unable to fetch page", 'HTTP 404 {"status":404}')
    monkeypatch.setattr(telecom_module, "_client", lambda: _FakeClient(error))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Vonage must not be called when failover is disabled")

    monkeypatch.setattr(telecom_module.secondary, "search_available_numbers", _fail_if_called)

    try:
        search_available_numbers("IN", number_type="mobile")
        assert False, "expected NoCoverageError"
    except NoCoverageError:
        pass


def test_twilio_search_results_are_tagged_with_the_twilio_provider(monkeypatch):
    class _FakeNumber:
        phone_number = "+15551234567"
        locality = "Test City"
        region = "TS"
        capabilities = {"voice": True}
        address_requirements = "none"

    class _WorkingResource:
        def list(self, **kwargs):
            return [_FakeNumber()]

    class _WorkingCountry:
        def __init__(self):
            self.local = _WorkingResource()

    monkeypatch.setattr(telecom_module, "_client", lambda: type("C", (), {"available_phone_numbers": lambda self, c: _WorkingCountry()})())

    results = search_available_numbers("US", number_type="local")
    assert results[0]["provider"] == "twilio"


def test_buy_number_via_provider_dispatches_to_vonage(monkeypatch):
    called = []
    monkeypatch.setattr(telecom_module.secondary, "buy_number", lambda phone_number: called.append(phone_number) or {"sid": phone_number})

    def _fail_if_twilio_called(*args, **kwargs):
        raise AssertionError("Twilio's own buy_number must not be called for a Vonage-sourced number")

    monkeypatch.setattr(telecom_module, "buy_number", _fail_if_twilio_called)

    result = buy_number_via_provider("vonage", "+917039068350")
    assert called == ["+917039068350"]
    assert result["sid"] == "+917039068350"


def test_buy_number_via_provider_dispatches_to_twilio_by_default(monkeypatch):
    called = []
    monkeypatch.setattr(
        telecom_module, "buy_number",
        lambda phone_number, bundle_sid=None: called.append((phone_number, bundle_sid)) or {"sid": "PN_fake"},
    )

    def _fail_if_vonage_called(*args, **kwargs):
        raise AssertionError("Vonage must not be called for a Twilio-sourced number")

    monkeypatch.setattr(telecom_module.secondary, "buy_number", _fail_if_vonage_called)

    result = buy_number_via_provider("twilio", "+15551234567", bundle_sid="BU123")
    assert called == [("+15551234567", "BU123")]
    assert result["sid"] == "PN_fake"
