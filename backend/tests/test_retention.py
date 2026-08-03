from datetime import datetime, timedelta, timezone

from app.media.models import CallDirection, CallRecord, VideoSession, VideoSessionStatus, Voicemail
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

    staff_service.create_staff(db_session, email=email, password="staffpass123")
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


def test_member_cannot_set_retention_policy(client):
    owner_token = _signup_and_login(client, "retentionowner1@example.com", account_type="business")
    headers = {"Authorization": f"Bearer {owner_token}"}
    client.post(
        "/team/members",
        json={"email": "retentionmember1@example.com", "password": "supersecret123", "role": "member"},
        headers=headers,
    )
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
