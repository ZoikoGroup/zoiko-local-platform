from twilio.base.exceptions import TwilioException, TwilioRestException

from app.integrations.telecom.twilio import _clean_twilio_error_message


def test_raw_trial_account_dump_is_translated_to_a_customer_safe_message():
    """Confirmed live (2026-08-18): TwilioException's bare str() for this
    specific failure is a raw Python tuple/dict repr containing Twilio's own
    "upgrade your account" wording, aimed at us (the Twilio account holder),
    not the customer reading it."""
    e = TwilioException(
        "Unable to fetch page",
        '{"code":20003,"message":"This feature is not available on a Trial account. '
        'Please upgrade your account to gain access.","more_info":"https://www.twilio.com/docs/errors/20003","status":401}',
    )
    result = _clean_twilio_error_message(e)
    assert "trial account" not in result.lower()
    assert "upgrade your account" not in result.lower()
    assert "temporarily unavailable" in result.lower()


def test_clean_rest_exception_trial_message_is_also_translated():
    """The message can already be clean (via TwilioRestException.msg, no
    raw dump) and still need this translation - it's a wrong-audience
    problem, not a parsing problem."""
    e = TwilioRestException(
        status=401, uri="/AvailablePhoneNumbers", msg="This feature is not available on a Trial account.",
    )
    result = _clean_twilio_error_message(e)
    assert "temporarily unavailable" in result.lower()


def test_unrelated_error_messages_pass_through_unchanged():
    e = TwilioRestException(status=404, uri="/AvailablePhoneNumbers", msg="The requested resource was not found")
    assert _clean_twilio_error_message(e) == "The requested resource was not found"
