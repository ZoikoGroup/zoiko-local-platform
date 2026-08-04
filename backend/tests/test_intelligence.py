import pytest

from app.media.models import CallDirection, CallRecord, VideoSession, VideoSessionStatus, Voicemail
from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus


def _signup_and_login(client, email: str, account_name: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": account_name,
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _make_voicemail(db_session, account_id: str, e164: str, country: str = "US") -> Voicemail:
    number = PhoneNumber(e164=e164, country=country, status=PhoneNumberStatus.ACTIVE, account_id=account_id)
    db_session.add(number)
    db_session.commit()

    voicemail = Voicemail(
        phone_number_id=number.id,
        account_id=account_id,
        from_number="+15559990000",
        recording_url="https://example.com/fake.wav",
    )
    db_session.add(voicemail)
    db_session.commit()
    return voicemail


def _make_recorded_call(db_session, account_id: str, *, recording_url: str | None = "https://example.com/call.wav") -> CallRecord:
    call = CallRecord(
        account_id=account_id,
        phone_number_id=None,
        direction=CallDirection.INBOUND,
        from_number="+15559990000",
        to_number="+15550001111",
        provider_call_sid="CAintel1",
        status="completed",
        duration=90,
        recording_url=recording_url,
    )
    db_session.add(call)
    db_session.commit()
    return call


def test_summarize_call_rejects_other_account(client, db_session):
    _, owner_account_id = _signup_and_login(client, "intelcallowner@example.com", "Intel Call Owner Co")
    call = _make_recorded_call(db_session, owner_account_id)

    intruder_token, _ = _signup_and_login(client, "intelcallintruder@example.com", "Intel Call Intruder Co")
    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {intruder_token}"}
    )
    assert response.status_code == 403


def test_summarize_call_rejects_without_a_recording(client, db_session):
    token, account_id = _signup_and_login(client, "intelcallnorec@example.com", "Intel Call No Rec Co")
    call = _make_recorded_call(db_session, account_id, recording_url=None)

    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 409
    assert "no recording" in response.json()["detail"].lower()


def test_summarize_call_rejects_without_consent(client, db_session):
    token, account_id = _signup_and_login(client, "intelcallnoconsent@example.com", "Intel Call No Consent Co")
    call = _make_recorded_call(db_session, account_id)

    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()


@pytest.mark.live
def test_summarize_call_success_path(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "intelcallsuccess@example.com", "Intel Call Success Co")
    call = _make_recorded_call(db_session, account_id)

    consent_response = client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert consent_response.status_code == 200

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr(
        "app.intelligence.service.transcribe_audio",
        lambda audio_bytes: "Hi, calling about my order, can you call me back?",
    )

    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "call"
    assert body["transcript"] == "Hi, calling about my order, can you call me back?"
    assert body["summary"]
    assert body["model_version"]
    # real Groq call — structured fields, not just prose (the gap this
    # feature closes: language/urgency/action_items/suggested_follow_up
    # used to not exist at all).
    assert body["urgency"] in ("low", "medium", "high")
    assert isinstance(body["action_items"], list)
    assert body["language"] is None or isinstance(body["language"], str)


def test_summarize_voicemail_rejects_other_account(client, db_session):
    _, owner_account_id = _signup_and_login(client, "intelowner@example.com", "Intel Owner Co")
    voicemail = _make_voicemail(db_session, owner_account_id, "+15550004444")

    intruder_token, _ = _signup_and_login(client, "intelintruder@example.com", "Intel Intruder Co")

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_summarize_voicemail_rejects_without_consent(client, db_session):
    token, account_id = _signup_and_login(client, "intelnoconsent@example.com", "Intel No Consent Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550006666")

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()


@pytest.mark.live
def test_summarize_voicemail_succeeds_with_a_country_specific_consent_grant(client, db_session, monkeypatch):
    """Consent scoped to the voicemail's own number's country (not GLOBAL)
    must be enough - jurisdiction is derived from the number, not just a
    single account-wide flag."""
    token, account_id = _signup_and_login(client, "inteljurisus@example.com", "Intel Jurisdiction US Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550007777", country="US")

    consent_response = client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing", "jurisdiction": "US"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert consent_response.status_code == 200

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr("app.intelligence.service.transcribe_audio", lambda audio_bytes: "test transcript")

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text


def test_summarize_voicemail_country_specific_consent_does_not_cover_a_different_country(client, db_session):
    token, account_id = _signup_and_login(client, "inteljurisgb@example.com", "Intel Jurisdiction GB Co")
    voicemail = _make_voicemail(db_session, account_id, "+442079460001", country="GB")

    client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing", "jurisdiction": "US"},
        headers={"Authorization": f"Bearer {token}"},
    )

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()


@pytest.mark.live
def test_summarize_voicemail_success_path(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "intelsuccess@example.com", "Intel Success Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550005555")

    consent_response = client.post(
        "/compliance/consent",
        json={"consent_type": "ai_processing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert consent_response.status_code == 200

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr(
        "app.intelligence.service.transcribe_audio",
        lambda audio_bytes: "Hi, this is a test voicemail, please call me back.",
    )

    response = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["transcript"] == "Hi, this is a test voicemail, please call me back."
    assert body["summary"]  # real Groq call — non-empty summary text
    assert body["model_version"]
    assert body["disclaimer"]
    assert body["urgency"] in ("low", "medium", "high")
    assert isinstance(body["action_items"], list)

    list_response = client.get("/intelligence/summaries", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1

    revoke_response = client.delete(
        "/compliance/consent/ai_processing", headers={"Authorization": f"Bearer {token}"}
    )
    assert revoke_response.status_code == 200


def _make_video_session(
    db_session, account_id: str, host_user_id: str, *,
    recording_url: str | None = "https://storage.example.com/recordings/zl-test1.mp4",
    room_name: str = "zl-test1",
) -> VideoSession:
    session = VideoSession(
        account_id=account_id, host_user_id=host_user_id, room_name=room_name,
        status=VideoSessionStatus.ENDED, recording_url=recording_url,
    )
    db_session.add(session)
    db_session.commit()
    return session


def test_summarize_video_session_rejects_other_account(client, db_session):
    owner_token, owner_account_id = _signup_and_login(client, "intelvidowner@example.com", "Intel Video Owner Co")
    owner_me = client.get("/auth/me", headers={"Authorization": f"Bearer {owner_token}"}).json()
    session = _make_video_session(db_session, owner_account_id, owner_me["id"], room_name="zl-test-other-account")

    intruder_token, _ = _signup_and_login(client, "intelvidintruder@example.com", "Intel Video Intruder Co")
    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_summarize_video_session_rejects_without_a_recording(client, db_session):
    token, account_id = _signup_and_login(client, "intelvidnorec@example.com", "Intel Video No Rec Co")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    session = _make_video_session(db_session, account_id, me["id"], recording_url=None, room_name="zl-test-no-rec")

    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 409
    assert "no finished recording" in response.json()["detail"].lower()


def test_summarize_video_session_rejects_without_consent(client, db_session):
    token, account_id = _signup_and_login(client, "intelvidnoconsent@example.com", "Intel Video No Consent Co")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    session = _make_video_session(db_session, account_id, me["id"], room_name="zl-test-no-consent")

    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()


def test_summarize_video_session_rejects_member_who_did_not_host(client, db_session):
    """Same host-only restriction already enforced for starting a recording -
    a Member who didn't host the call can't pull its AI summary either, even
    though they can see other summaries on numbers assigned to them."""
    owner_token, owner_account_id = _signup_and_login(
        client, "intelvidhostowner@example.com", "Intel Video Host Owner Co"
    )
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    owner_me = client.get("/auth/me", headers=owner_headers).json()
    session = _make_video_session(db_session, owner_account_id, owner_me["id"], room_name="zl-test-host-only")

    client.post(
        "/team/members",
        json={"email": "intelvidhostmember@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "intelvidhostmember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403
    assert "not hosted by you" in response.json()["detail"].lower()


@pytest.mark.live
def test_summarize_video_session_success_path(client, db_session, monkeypatch):
    token, account_id = _signup_and_login(client, "intelvidsuccess@example.com", "Intel Video Success Co")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    session = _make_video_session(db_session, account_id, me["id"], room_name="zl-test-success")

    consent_response = client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert consent_response.status_code == 200

    monkeypatch.setattr("app.intelligence.service.download_object", lambda key: b"fake-video-bytes")
    monkeypatch.setattr(
        "app.intelligence.service.transcribe_audio",
        lambda audio_bytes, filename=None, content_type=None: "Let's review the Q3 roadmap on this call.",
    )

    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["source_type"] == "video"
    assert body["transcript"] == "Let's review the Q3 roadmap on this call."
    assert body["summary"]
    assert body["model_version"]
    assert body["urgency"] in ("low", "medium", "high")
    assert isinstance(body["action_items"], list)


def _seed_summary(db_session, account_id: str, *, summary: str, transcript: str) -> None:
    from app.intelligence.models import ConversationSummary, SummarySourceType

    db_session.add(
        ConversationSummary(
            account_id=account_id, source_type=SummarySourceType.VOICEMAIL, source_id=account_id,
            transcript=transcript, summary=summary, model_version="groq/test",
        )
    )
    db_session.commit()


def test_search_summaries_finds_a_matching_record_by_content(client, db_session):
    token, account_id = _signup_and_login(client, "search1@example.com", "Search Test Co")
    _seed_summary(
        db_session, account_id,
        summary="Caller reported a billing dispute over their last invoice.",
        transcript="Hi, I think I was overcharged on my last invoice, can someone call me back.",
    )
    _seed_summary(
        db_session, account_id,
        summary="Caller wants to upgrade their video plan.",
        transcript="Hey, I'd like to add more video minutes to my account please.",
    )

    response = client.get(
        "/intelligence/summaries/search", params={"q": "billing invoice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert "billing" in results[0]["summary"].lower()


def test_search_summaries_returns_empty_for_no_match(client, db_session):
    token, account_id = _signup_and_login(client, "search2@example.com", "Search Test Co 2")
    _seed_summary(
        db_session, account_id,
        summary="Caller wants to upgrade their video plan.",
        transcript="Hey, I'd like to add more video minutes to my account please.",
    )

    response = client.get(
        "/intelligence/summaries/search", params={"q": "completely unrelated topic xyz"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_search_summaries_is_scoped_to_the_callers_own_account(client, db_session):
    token_a, account_a = _signup_and_login(client, "search3a@example.com", "Search Test Co 3A")
    _, account_b = _signup_and_login(client, "search3b@example.com", "Search Test Co 3B")
    _seed_summary(
        db_session, account_b,
        summary="Caller reported a billing dispute over their invoice.",
        transcript="I was overcharged, please call me back about billing.",
    )

    response = client.get(
        "/intelligence/summaries/search", params={"q": "billing"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_search_summaries_requires_auth(client):
    response = client.get("/intelligence/summaries/search", params={"q": "billing"})
    assert response.status_code == 401


def test_search_summaries_with_blank_query_returns_empty(client, db_session):
    token, account_id = _signup_and_login(client, "search4@example.com", "Search Test Co 4")
    _seed_summary(db_session, account_id, summary="Anything", transcript="Anything at all")

    response = client.get(
        "/intelligence/summaries/search", params={"q": "   "}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.json() == []


def _seed_call_summary(db_session, account_id: str, *, summary: str = "Original AI summary."):
    from app.intelligence.models import ConversationSummary, SummarySourceType

    call = CallRecord(
        account_id=account_id, phone_number_id=None, direction=CallDirection.INBOUND,
        from_number="+15559990000", to_number="+15550001111", provider_call_sid="CAeditsum1",
        status="completed", duration=60, recording_url="https://example.com/call.wav",
    )
    db_session.add(call)
    db_session.commit()

    record = ConversationSummary(
        account_id=account_id, source_type=SummarySourceType.CALL, source_id=call.id,
        transcript="Hi, calling about my order.", summary=summary, model_version="groq/test",
    )
    db_session.add(record)
    db_session.commit()
    return record


def test_edit_summary_updates_text_and_preserves_the_original(client, db_session):
    token, account_id = _signup_and_login(client, "editsum1@example.com", "Edit Summary Co 1")
    record = _seed_call_summary(db_session, account_id, summary="Original AI summary.")

    response = client.patch(
        f"/intelligence/summaries/{record.id}", json={"summary": "Corrected: caller wants a refund, not a repair."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"] == "Corrected: caller wants a refund, not a repair."
    assert body["original_summary"] == "Original AI summary."
    assert body["edited_at"] is not None


def test_edit_summary_twice_keeps_the_first_original(client, db_session):
    token, account_id = _signup_and_login(client, "editsum2@example.com", "Edit Summary Co 2")
    record = _seed_call_summary(db_session, account_id, summary="First AI summary.")

    client.patch(
        f"/intelligence/summaries/{record.id}", json={"summary": "First correction."},
        headers={"Authorization": f"Bearer {token}"},
    )
    response = client.patch(
        f"/intelligence/summaries/{record.id}", json={"summary": "Second correction."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "Second correction."
    # original_summary must still be the very first AI output, not the
    # first edit - it's only ever set once.
    assert body["original_summary"] == "First AI summary."


def test_edit_summary_rejects_other_account(client, db_session):
    _, owner_account_id = _signup_and_login(client, "editsum3owner@example.com", "Edit Summary Co 3")
    record = _seed_call_summary(db_session, owner_account_id)

    intruder_token, _ = _signup_and_login(client, "editsum3intruder@example.com", "Edit Summary Co 3 Intruder")
    response = client.patch(
        f"/intelligence/summaries/{record.id}", json={"summary": "Hijacked."},
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


def test_edit_summary_requires_auth(client, db_session):
    _, account_id = _signup_and_login(client, "editsum4@example.com", "Edit Summary Co 4")
    record = _seed_call_summary(db_session, account_id)

    response = client.patch(f"/intelligence/summaries/{record.id}", json={"summary": "x"})
    assert response.status_code == 401
