from datetime import datetime, timedelta, timezone

from app.media.models import CallDirection, CallRecord, ReceptionistCall, VideoSession, VideoSessionStatus, Voicemail
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str, account_type: str = "individual") -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Retention Test Co",
            "account_type": account_type,
            "email": email,
            "password": "supersecret123",
        },
    )
    response = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return response.json()["access_token"]


def _create_and_login_staff(db_session, client, email: str) -> str:
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=PlatformStaffRole.SUPER_ADMIN)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def test_list_policies_requires_auth(client):
    response = client.get("/retention/policies")
    assert response.status_code == 401


def test_list_policies_returns_default_90_days_when_unconfigured(client):
    token = _signup_and_login(client, "retentiondefault@example.com")
    response = client.get("/retention/policies", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"voicemail": 90, "call_recording": 90, "video_recording": 90}


def test_member_cannot_set_retention_policy(client, db_session):
    owner_token = _signup_and_login(client, "retentionowner1@example.com", account_type="business")
    headers = {"Authorization": f"Bearer {owner_token}"}

    # team.members.enabled (ZL-COM-ENT-001 §7) is Business+ only - a fresh
    # signup defaults to free_trial, which would 402 the invite below
    # before this test ever reaches its actual point (a member, once
    # added, is blocked from retention policy specifically).
    from app.billing import service as billing_service

    account_id = client.get("/auth/me", headers=headers).json()["account_id"]
    billing_service.change_plan(db_session, account_id, "business", actor="test-setup")

    invite = client.post(
        "/team/members",
        json={"email": "retentionmember1@example.com", "password": "supersecret123", "role": "member"},
        headers=headers,
    )
    assert invite.status_code == 201, invite.text
    member_token = client.post(
        "/auth/login", json={"email": "retentionmember1@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.put(
        "/retention/policies/voicemail",
        json={"retention_days": 30},
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403


def test_owner_can_set_and_see_a_retention_override(client):
    token = _signup_and_login(client, "retentionowner2@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    set_response = client.put("/retention/policies/voicemail", json={"retention_days": 30}, headers=headers)
    assert set_response.status_code == 200
    assert set_response.json() == {"artifact_type": "voicemail", "retention_days": 30}

    list_response = client.get("/retention/policies", headers=headers)
    body = list_response.json()
    assert body["voicemail"] == 30
    assert body["call_recording"] == 90  # untouched, still the default


def test_set_retention_policy_rejects_zero_or_negative(client):
    token = _signup_and_login(client, "retentionowner3@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.put("/retention/policies/voicemail", json={"retention_days": 0}, headers=headers)
    assert response.status_code == 422


def test_purge_requires_staff_auth(client):
    response = client.post("/retention/purge")
    assert response.status_code == 401


def test_customer_cannot_trigger_purge(client):
    token = _signup_and_login(client, "retentionpurgecustomer@example.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def _make_voicemail(db_session, account_id: str, e164: str, created_at: datetime) -> Voicemail:
    number = PhoneNumber(e164=e164, country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()

    voicemail = Voicemail(
        phone_number_id=number.id,
        account_id=account_id,
        from_number="+15559990000",
        recording_url="https://api.twilio.com/2010-04-01/Accounts/ACxxx/Recordings/REtest123",
        created_at=created_at,
    )
    db_session.add(voicemail)
    db_session.commit()
    return voicemail


def test_purge_removes_a_voicemail_recording_past_retention(client, db_session, monkeypatch):
    deleted_sids = []
    monkeypatch.setattr(
        "app.retention.service.telecom.delete_recording", lambda sid: deleted_sids.append(sid)
    )

    account_id = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Test Co",
            "account_type": "individual",
            "email": "purgevoicemail@example.com",
            "password": "supersecret123",
        },
    ).json()["account_id"]
    old = datetime.now(timezone.utc) - timedelta(days=200)
    _make_voicemail(db_session, account_id, "+15550001111", created_at=old)

    staff_token = _create_and_login_staff(db_session, client, "purgestaff1@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.status_code == 200
    assert response.json()["voicemail"] == {"purged": 1, "failed": 0}
    assert deleted_sids == ["REtest123"]

    vm = db_session.query(Voicemail).filter(Voicemail.account_id == account_id).first()
    assert vm.recording_url == "[deleted - retention policy]"


def test_purge_leaves_a_recent_voicemail_untouched(client, db_session, monkeypatch):
    monkeypatch.setattr("app.retention.service.telecom.delete_recording", lambda sid: None)

    account_id = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Recent Co",
            "account_type": "individual",
            "email": "purgerecent@example.com",
            "password": "supersecret123",
        },
    ).json()["account_id"]
    recent = datetime.now(timezone.utc) - timedelta(days=5)
    _make_voicemail(db_session, account_id, "+15550002222", created_at=recent)

    staff_token = _create_and_login_staff(db_session, client, "purgestaff2@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.json()["voicemail"] == {"purged": 0, "failed": 0}

    vm = db_session.query(Voicemail).filter(Voicemail.account_id == account_id).first()
    assert vm.recording_url != "[deleted - retention policy]"


def test_purge_respects_an_account_specific_override(client, db_session, monkeypatch):
    monkeypatch.setattr("app.retention.service.telecom.delete_recording", lambda sid: None)

    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Override Co",
            "account_type": "individual",
            "email": "purgeoverride@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    token = client.post(
        "/auth/login", json={"email": "purgeoverride@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    # Shrink this account's voicemail retention to 3 days
    client.put(
        "/retention/policies/voicemail",
        json={"retention_days": 3},
        headers={"Authorization": f"Bearer {token}"},
    )

    ten_days_old = datetime.now(timezone.utc) - timedelta(days=10)
    _make_voicemail(db_session, account_id, "+15550003333", created_at=ten_days_old)

    staff_token = _create_and_login_staff(db_session, client, "purgestaff3@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    # Would NOT have been purged under the 90-day default, but the account's
    # own 3-day override makes a 10-day-old voicemail expired
    assert response.json()["voicemail"] == {"purged": 1, "failed": 0}


def test_purge_leaves_recording_url_untouched_when_provider_deletion_fails(client, db_session, monkeypatch):
    from app.integrations.telecom.twilio import TelecomError

    def _fail(sid):
        raise TelecomError("boom")

    monkeypatch.setattr("app.retention.service.telecom.delete_recording", _fail)

    account_id = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Fail Co",
            "account_type": "individual",
            "email": "purgefail@example.com",
            "password": "supersecret123",
        },
    ).json()["account_id"]
    old = datetime.now(timezone.utc) - timedelta(days=200)
    original_url = "https://api.twilio.com/2010-04-01/Accounts/ACxxx/Recordings/REstillthere"
    number = PhoneNumber(e164="+15550004444", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()
    voicemail = Voicemail(
        phone_number_id=number.id, account_id=account_id, from_number="+15559990000",
        recording_url=original_url, created_at=old,
    )
    db_session.add(voicemail)
    db_session.commit()

    staff_token = _create_and_login_staff(db_session, client, "purgestaff4@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.json()["voicemail"] == {"purged": 0, "failed": 1}

    db_session.refresh(voicemail)
    assert voicemail.recording_url == original_url  # untouched - safe to retry next run


def test_purge_removes_a_call_recording_past_retention(client, db_session, monkeypatch):
    deleted_sids = []
    monkeypatch.setattr(
        "app.retention.service.telecom.delete_recording", lambda sid: deleted_sids.append(sid)
    )

    account_id = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Call Co",
            "account_type": "individual",
            "email": "purgecall@example.com",
            "password": "supersecret123",
        },
    ).json()["account_id"]
    old = datetime.now(timezone.utc) - timedelta(days=200)
    call = CallRecord(
        account_id=account_id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550005555", provider_call_sid="CAtest1",
        status="completed", duration=60,
        recording_url="https://api.twilio.com/2010-04-01/Accounts/ACxxx/Recordings/REcalltest",
        created_at=old,
    )
    db_session.add(call)
    db_session.commit()

    staff_token = _create_and_login_staff(db_session, client, "purgestaff5@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.json()["call_recording"] == {"purged": 1, "failed": 0}
    assert deleted_sids == ["REcalltest"]


def test_purge_removes_a_video_recording_past_retention(client, db_session, monkeypatch):
    deleted_keys = []
    monkeypatch.setattr(
        "app.retention.service.delete_object", lambda key: deleted_keys.append(key)
    )

    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Video Co",
            "account_type": "individual",
            "email": "purgevideo@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    me = client.post(
        "/auth/login", json={"email": "purgevideo@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {me}"}).json()["id"]

    old = datetime.now(timezone.utc) - timedelta(days=200)
    session = VideoSession(
        account_id=account_id, host_user_id=user_id, room_name="zl-purgetest1",
        status=VideoSessionStatus.ENDED, started_at=old, ended_at=old,
        recording_url="https://s3.example.com/zoiko-local-video-recordings/recordings/zl-purgetest1.mp4",
    )
    db_session.add(session)
    db_session.commit()

    staff_token = _create_and_login_staff(db_session, client, "purgestaff6@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.json()["video_recording"] == {"purged": 1, "failed": 0}
    assert deleted_keys == ["recordings/zl-purgetest1.mp4"]


def test_purge_leaves_video_recording_url_untouched_when_storage_deletion_fails(client, db_session, monkeypatch):
    """Video-recording analog of test_purge_leaves_recording_url_untouched_
    when_provider_deletion_fails above - the S3/boto3 call site had zero
    genuine-failure coverage before this."""
    from app.integrations.storage.s3 import StorageError

    def _fail(key):
        raise StorageError("boom")

    monkeypatch.setattr("app.retention.service.delete_object", _fail)

    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Purge Video Fail Co",
            "account_type": "individual",
            "email": "purgevideofail@example.com",
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    me = client.post(
        "/auth/login", json={"email": "purgevideofail@example.com", "password": "supersecret123"}
    ).json()["access_token"]
    user_id = client.get("/auth/me", headers={"Authorization": f"Bearer {me}"}).json()["id"]

    old = datetime.now(timezone.utc) - timedelta(days=200)
    original_url = "https://s3.example.com/zoiko-local-video-recordings/recordings/zl-purgefailtest.mp4"
    session = VideoSession(
        account_id=account_id, host_user_id=user_id, room_name="zl-purgefailtest",
        status=VideoSessionStatus.ENDED, started_at=old, ended_at=old,
        recording_url=original_url,
    )
    db_session.add(session)
    db_session.commit()

    staff_token = _create_and_login_staff(db_session, client, "purgestaff7@zoikolocal.com")
    response = client.post("/retention/purge", headers={"Authorization": f"Bearer {staff_token}"})
    assert response.json()["video_recording"] == {"purged": 0, "failed": 1}

    db_session.refresh(session)
    assert session.recording_url == original_url  # untouched - safe to retry next run


# --- erase_account_data / right-to-erasure (real gap fix) ---


def _make_contact(db_session, account_id: str) -> "Contact":
    from app.contacts.models import Contact

    contact = Contact(account_id=account_id, name="Jane Erasable", phone_number="+15551230000")
    db_session.add(contact)
    db_session.commit()
    return contact


def _make_conversation_summary(db_session, account_id: str) -> "ConversationSummary":
    from app.intelligence.models import ConversationSummary, SummarySourceType
    from app.core.ids import new_uuid

    summary = ConversationSummary(
        account_id=account_id, source_type=SummarySourceType.VOICEMAIL, source_id=new_uuid(),
        transcript="hello this is a real transcript", summary="caller left a message",
        model_version="test-fixture",
    )
    db_session.add(summary)
    db_session.commit()
    return summary


def _make_receptionist_call(db_session, account_id: str, phone_number_id: str) -> ReceptionistCall:
    call = ReceptionistCall(
        account_id=account_id, phone_number_id=phone_number_id, call_sid=f"CA_erasetest_{account_id}",
        caller_number="+15559998888", raw_transcript="my real phone number is 555-0000, call me back",
        caller_name="Jane Caller", caller_company="Jane Co", reason="wants a callback",
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_erase_account_data_raises_when_account_under_legal_hold(db_session):
    from app.numbering.identity.models import Account, AccountType
    from app.retention.service import AccountUnderLegalHoldError, erase_account_data

    account = Account(name="Erase Legal Hold Co", account_type=AccountType.INDIVIDUAL, legal_hold=True)
    db_session.add(account)
    db_session.commit()

    _make_contact(db_session, account.id)

    try:
        erase_account_data(db_session, account.id, actor="staff-1")
        assert False, "expected AccountUnderLegalHoldError"
    except AccountUnderLegalHoldError:
        pass

    # Nothing was touched - the hold blocked the erasure before any deletion.
    from app.contacts.models import Contact

    assert db_session.query(Contact).filter(Contact.account_id == account.id).count() == 1


def test_erase_account_data_deletes_contacts_and_summaries_and_redacts_receptionist_calls(db_session, monkeypatch):
    from app.contacts.models import Contact
    from app.intelligence.models import ConversationSummary
    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Erase Full Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.commit()

    number = PhoneNumber(e164="+15550002222", country="US", status=PhoneNumberStatus.ACTIVE, account_id=account.id)
    db_session.add(number)
    db_session.commit()

    _make_contact(db_session, account.id)
    _make_conversation_summary(db_session, account.id)
    call = _make_receptionist_call(db_session, account.id, number.id)

    from app.retention.service import erase_account_data

    result = erase_account_data(db_session, account.id, actor="staff-1")

    assert result["contacts_deleted"] == 1
    assert result["summaries_deleted"] == 1
    assert result["receptionist_calls_redacted"] == 1
    assert db_session.query(Contact).filter(Contact.account_id == account.id).count() == 0
    assert db_session.query(ConversationSummary).filter(ConversationSummary.account_id == account.id).count() == 0

    db_session.refresh(call)
    assert "555-0000" not in call.raw_transcript
    assert call.caller_name is None
    assert call.caller_company is None
    assert call.reason is None
    assert call.caller_number == "[erased]"


def test_erase_account_data_force_purges_a_recent_recording_ignoring_retention_window(db_session, monkeypatch):
    deleted_sids = []
    monkeypatch.setattr(
        "app.retention.service.telecom.delete_recording", lambda sid: deleted_sids.append(sid)
    )
    from app.numbering.identity.models import Account, AccountType

    account = Account(name="Erase Recent Recording Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.commit()

    recent = datetime.now(timezone.utc) - timedelta(days=1)  # nowhere near the 90-day default retention window
    _make_voicemail(db_session, account.id, "+15550003333", created_at=recent)

    from app.retention.service import erase_account_data

    result = erase_account_data(db_session, account.id, actor="staff-1")

    assert result["voicemails_purged"] == 1
    assert deleted_sids == ["REtest123"]

    vm = db_session.query(Voicemail).filter(Voicemail.account_id == account.id).first()
    assert vm.recording_url == "[deleted - retention policy]"


def test_resolve_erasure_request_completing_actually_erases_the_data(db_session):
    from app.numbering.identity.models import Account, AccountType
    from app.retention.models import ErasureRequestStatus
    from app.retention.service import create_erasure_request, resolve_erasure_request

    account = Account(name="Resolve Erasure Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.commit()
    _make_contact(db_session, account.id)

    request = create_erasure_request(db_session, account_id=account.id, requested_by=account.id, notes="delete me")
    resolve_erasure_request(
        db_session, request.id, status=ErasureRequestStatus.COMPLETED, resolution_notes="done", actor="staff-1",
    )

    from app.contacts.models import Contact

    assert db_session.query(Contact).filter(Contact.account_id == account.id).count() == 0


def test_resolve_erasure_request_completing_refuses_when_account_under_legal_hold(db_session):
    from app.numbering.identity.models import Account, AccountType
    from app.retention.models import ErasureRequestStatus
    from app.retention.service import AccountUnderLegalHoldError, create_erasure_request, resolve_erasure_request

    # create_erasure_request itself now also refuses to open a request
    # against an account already under legal hold - so to isolate the
    # thing this test actually checks (resolve_erasure_request refuses
    # too), the hold has to start AFTER the request already exists, not
    # before it.
    account = Account(name="Resolve Erasure Hold Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.commit()

    request = create_erasure_request(db_session, account_id=account.id, requested_by=account.id, notes="delete me")

    account.legal_hold = True
    db_session.commit()

    try:
        resolve_erasure_request(
            db_session, request.id, status=ErasureRequestStatus.COMPLETED, resolution_notes="done", actor="staff-1",
        )
        assert False, "expected AccountUnderLegalHoldError"
    except AccountUnderLegalHoldError:
        pass

    db_session.refresh(request)
    assert request.status == ErasureRequestStatus.PENDING  # never advanced past PENDING


def test_erase_single_call_content_erases_recording_and_summary_only_for_that_call(db_session, monkeypatch):
    """The precise, single-record version of erase_account_data - a
    customer wants ONE call's recording/AI content gone (e.g. it captured
    something personal) without touching anything else on the account,
    including a second, unrelated call's own recording/summary."""
    from app.intelligence.models import ConversationSummary, SummarySourceType
    from app.numbering.identity.models import Account, AccountType
    from app.retention.service import erase_single_call_content

    deleted_sids = []
    monkeypatch.setattr(
        "app.retention.service.telecom.delete_recording", lambda sid: deleted_sids.append(sid)
    )

    account = Account(name="Erase Single Call Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.commit()

    target_call = CallRecord(
        account_id=account.id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid="CAerasetarget1",
        status="completed", duration=660,
        recording_url="https://api.twilio.com/2010-04-01/Accounts/ACxxx/Recordings/REtargetsid",
    )
    other_call = CallRecord(
        account_id=account.id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559991111", to_number="+15550001111", provider_call_sid="CAeraseother1",
        status="completed", duration=120,
        recording_url="https://api.twilio.com/2010-04-01/Accounts/ACxxx/Recordings/REothersid",
    )
    db_session.add_all([target_call, other_call])
    db_session.commit()

    target_summary = ConversationSummary(
        account_id=account.id, source_type=SummarySourceType.CALL, source_id=target_call.id,
        transcript="a real personal conversation", summary="personal call summary",
        model_version="test-fixture",
    )
    other_summary = ConversationSummary(
        account_id=account.id, source_type=SummarySourceType.CALL, source_id=other_call.id,
        transcript="an unrelated call", summary="unrelated call summary",
        model_version="test-fixture",
    )
    db_session.add_all([target_summary, other_summary])
    db_session.commit()

    result = erase_single_call_content(db_session, account.id, target_call.id, actor="owner-1")

    assert result == {"recording_erased": True, "summaries_deleted": 1}
    assert deleted_sids == ["REtargetsid"]  # only the target call's recording, never the other one

    db_session.refresh(target_call)
    assert target_call.recording_url == "[erased - right to erasure]"
    # Bare entry preserved - not the "erase everything" cascade.
    assert target_call.from_number == "+15559990000"
    assert target_call.duration == 660
    assert target_call.status == "completed"

    db_session.refresh(other_call)
    assert other_call.recording_url == "https://api.twilio.com/2010-04-01/Accounts/ACxxx/Recordings/REothersid"

    remaining_summaries = db_session.query(ConversationSummary).filter(
        ConversationSummary.account_id == account.id
    ).all()
    assert len(remaining_summaries) == 1
    assert remaining_summaries[0].id == other_summary.id


def test_erase_single_call_content_rejects_a_call_owned_by_another_account(db_session):
    from app.numbering.identity.models import Account, AccountType
    from app.retention.service import CallNotFoundError, erase_single_call_content

    owner_account = Account(name="Real Owner Co", account_type=AccountType.INDIVIDUAL)
    other_account = Account(name="Other Account Co", account_type=AccountType.INDIVIDUAL)
    db_session.add_all([owner_account, other_account])
    db_session.commit()

    call = CallRecord(
        account_id=owner_account.id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid="CAeraseauth1",
        status="completed", duration=60,
    )
    db_session.add(call)
    db_session.commit()

    try:
        erase_single_call_content(db_session, other_account.id, call.id, actor="attacker")
        assert False, "expected CallNotFoundError"
    except CallNotFoundError:
        pass


def test_erase_single_call_content_raises_when_account_under_legal_hold(db_session):
    from app.numbering.identity.models import Account, AccountType
    from app.retention.service import AccountUnderLegalHoldError, erase_single_call_content

    account = Account(name="Legal Hold Single Call Co", account_type=AccountType.INDIVIDUAL, legal_hold=True)
    db_session.add(account)
    db_session.commit()

    call = CallRecord(
        account_id=account.id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid="CAeraselegal1",
        status="completed", duration=60,
    )
    db_session.add(call)
    db_session.commit()

    try:
        erase_single_call_content(db_session, account.id, call.id, actor="owner-1")
        assert False, "expected AccountUnderLegalHoldError"
    except AccountUnderLegalHoldError:
        pass
