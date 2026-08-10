from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Risk Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _create_staff_and_login(client, db_session, email: str, role):
    from app.staff import service as staff_service

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def _active_number(db_session, account_id: str, e164: str) -> PhoneNumber:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    return number


def test_customer_cannot_manage_blocked_destinations(client):
    token = _signup_and_login(client, "riskcustomer@example.com")
    response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_support_staff_cannot_add_blocked_destination(client, db_session):
    from app.staff.models import PlatformStaffRole

    staff_token = _create_staff_and_login(client, db_session, "risksupport1@zoikolocal.com", PlatformStaffRole.SUPPORT)
    response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 403


def test_super_admin_can_add_and_support_staff_can_list(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskadmin1@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    create_response = client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert create_response.status_code == 201

    support_token = _create_staff_and_login(
        client, db_session, "risksupport2@zoikolocal.com", PlatformStaffRole.SUPPORT
    )
    list_response = client.get(
        "/risk/blocked-destinations", headers={"Authorization": f"Bearer {support_token}"}
    )
    assert list_response.status_code == 200
    assert any(r["prefix"] == "+1900" for r in list_response.json())


def test_outbound_call_to_blocked_destination_is_rejected(client, db_session):
    from app.staff.models import PlatformStaffRole

    admin_token = _create_staff_and_login(
        client, db_session, "riskadmin2@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    client.post(
        "/risk/blocked-destinations",
        json={"prefix": "+1900", "reason": "premium-rate scam prefix"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    token = _signup_and_login(client, "riskblocked@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550009999")

    response = client.post(
        "/media/voice/outbound",
        json={"to": "+19005551234", "from": "+15550009999"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "blocked destination" in response.json()["detail"].lower()


def test_outbound_call_velocity_limit_is_enforced(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAvelocity", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )

    token = _signup_and_login(client, "riskvelocity@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    _active_number(db_session, account_id, "+15550008888")
    headers = {"Authorization": f"Bearer {token}"}

    from app.risk.service import MAX_OUTBOUND_CALLS_PER_WINDOW

    for _ in range(MAX_OUTBOUND_CALLS_PER_WINDOW):
        response = client.post(
            "/media/voice/outbound",
            json={"to": "+15551230000", "from": "+15550008888"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    over_limit_response = client.post(
        "/media/voice/outbound",
        json={"to": "+15551230000", "from": "+15550008888"},
        headers=headers,
    )
    assert over_limit_response.status_code == 429


def _real_accounts(db_session, n: int, prefix: str) -> list[str]:
    """CallRecord.account_id is a real FK into accounts - unlike a plain
    string, Postgres will reject a fake id, so tests that write CallRecord
    rows directly need real Account rows behind them."""
    from app.numbering.identity.models import Account, AccountType

    ids = []
    for i in range(n):
        account = Account(name=f"{prefix}-{i}", account_type=AccountType.BUSINESS)
        db_session.add(account)
        db_session.flush()
        ids.append(account.id)
    return ids


def test_is_suspected_spam_caller_below_threshold(db_session):
    """Roadmap 'AI-driven fraud/spam signals': a real customer calling one
    or two different businesses is normal traffic, not a signal."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import is_suspected_spam_caller

    for account_id in _real_accounts(db_session, 2, "spamtest-below"):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND,
                from_number="+15559990001", to_number="+15550000001", status="ringing",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990001") is False


def test_is_suspected_spam_caller_at_threshold(db_session):
    """The same number fanning out across INBOUND_SPAM_ACCOUNT_THRESHOLD
    distinct accounts in the window IS the signal - no single account's own
    call history could ever surface this pattern on its own."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import INBOUND_SPAM_ACCOUNT_THRESHOLD, is_suspected_spam_caller

    for account_id in _real_accounts(db_session, INBOUND_SPAM_ACCOUNT_THRESHOLD, "spamtest-at"):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND,
                from_number="+15559990002", to_number="+15550000002", status="ringing",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990002") is True


def test_is_suspected_spam_caller_ignores_repeat_calls_to_the_same_account(db_session):
    """Calling the SAME business 5 times isn't fan-out - it's one distinct
    account, however many times they called."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import is_suspected_spam_caller

    account_id = _real_accounts(db_session, 1, "spamtest-loyal")[0]
    for _ in range(5):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.INBOUND,
                from_number="+15559990003", to_number="+15550000003", status="ringing",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990003") is False


def test_is_suspected_spam_caller_ignores_outbound_calls(db_session):
    """The velocity signal is about calls landing on many businesses, not
    an account placing many outbound calls (that's the separate
    assert_outbound_velocity_ok gate above)."""
    from app.media.models import CallDirection, CallRecord
    from app.risk.service import INBOUND_SPAM_ACCOUNT_THRESHOLD, is_suspected_spam_caller

    for account_id in _real_accounts(db_session, INBOUND_SPAM_ACCOUNT_THRESHOLD, "spamtest-outbound"):
        db_session.add(
            CallRecord(
                account_id=account_id, direction=CallDirection.OUTBOUND,
                from_number="+15550000004", to_number="+15559990004", status="queued",
            )
        )
    db_session.commit()

    assert is_suspected_spam_caller(db_session, "+15559990004") is False


def test_inbound_call_fanning_out_across_accounts_gets_flagged_via_the_real_webhook(client, db_session):
    """End-to-end: the same caller dialing INBOUND_SPAM_ACCOUNT_THRESHOLD
    distinct accounts' numbers through the real /media/voice/incoming
    webhook gets flagged on the call that crosses the threshold - proving
    the signal fires from real call traffic, not just direct service calls."""
    from app.risk.service import INBOUND_SPAM_ACCOUNT_THRESHOLD

    spam_caller = "+15559990005"
    tokens = []
    for i in range(INBOUND_SPAM_ACCOUNT_THRESHOLD):
        token = _signup_and_login(client, f"riskfanout{i}@example.com")
        account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
        _active_number(db_session, account_id, f"+1555000100{i}")
        tokens.append(token)

        url = "http://testserver/media/voice/incoming"
        params = {
            "To": f"+1555000100{i}", "From": spam_caller, "CallSid": f"CAfanout{i}", "CallStatus": "ringing",
        }
        signature = _twilio_signature(url, params)
        response = client.post("/media/voice/incoming", data=params, headers={"X-Twilio-Signature": signature})
        assert response.status_code == 200

    last_account_calls = client.get(
        "/media/voice/calls", headers={"Authorization": f"Bearer {tokens[-1]}"}
    ).json()
    assert len(last_account_calls) == 1
    assert last_account_calls[0]["is_suspected_spam"] is True

    first_account_calls = client.get(
        "/media/voice/calls", headers={"Authorization": f"Bearer {tokens[0]}"}
    ).json()
    assert first_account_calls[0]["is_suspected_spam"] is False
