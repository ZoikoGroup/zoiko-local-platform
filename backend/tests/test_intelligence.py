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
    token = login.json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (summarizing a
    # call/voicemail/video session, editing a summary) for a TRIALING
    # account, and this file's tests are about AI-summarization mechanics,
    # not trial-gating, so upgrade to a real paid plan here rather than
    # adding this to every individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token, account_id


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


def test_summarize_call_returns_a_clean_502_when_groq_summarization_fails(client, db_session, monkeypatch):
    """Chaos test: extract_conversation_summary raising LLMError (a real
    Groq outage/timeout, not a missing API key) must not become an
    unhandled 500 - and no ConversationSummary row should be left behind
    half-written."""
    from app.integrations.llm.groq import LLMError
    from app.intelligence.models import ConversationSummary

    token, account_id = _signup_and_login(client, "intelcallgroqdown@example.com", "Intel Call Groq Down Co")
    call = _make_recorded_call(db_session, account_id)
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr("app.intelligence.service.transcribe_audio", lambda audio_bytes: "test transcript")

    def _raise(*args, **kwargs):
        raise LLMError("Groq summarization request failed: connection timed out")

    monkeypatch.setattr("app.intelligence.service.extract_conversation_summary", _raise)

    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 502

    assert db_session.query(ConversationSummary).filter(ConversationSummary.source_id == call.id).count() == 0


def test_summarize_call_falls_back_when_groq_returns_a_null_summary(client, db_session, monkeypatch):
    """Confirmed live against the real Groq API: the configured model
    sometimes returns an explicit `"summary": null` for a very short/low-
    content transcript (e.g. "Hai, Helo, this is Renky.") even though the
    prompt never lists null as valid for that field. conversation_summaries
    .summary is NOT NULL (unlike every other AI-extracted field, which is
    nullable by design) - before the fix, this crashed the INSERT with a
    NotNullViolation that the DBAPIError handler turned into an opaque
    "Service temporarily unavailable" 503, hiding the real cause entirely."""
    from app.intelligence.models import ConversationSummary

    token, account_id = _signup_and_login(client, "intelcallnullsummary@example.com", "Intel Call Null Summary Co")
    call = _make_recorded_call(db_session, account_id)
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr("app.intelligence.service.transcribe_audio", lambda audio_bytes: "Hai, Helo, this is Renky.")
    monkeypatch.setattr(
        "app.intelligence.service.extract_conversation_summary",
        lambda transcript: {"summary": None, "language": "en", "urgency": "low", "action_items": [], "suggested_follow_up": None},
    )

    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201
    assert response.json()["summary"]  # never blank/None - a real fallback string

    stored = db_session.query(ConversationSummary).filter(ConversationSummary.source_id == call.id).first()
    assert stored is not None
    assert stored.summary


def test_summarize_call_degrades_gracefully_when_embedding_generation_fails(client, db_session, monkeypatch):
    """A Cohere outage must not block the summary itself - the documented
    degrade-gracefully behavior in _analyze_and_store's try/except, proven
    here rather than just asserted in a comment."""
    from app.integrations.embeddings.cohere import EmbeddingError

    token, account_id = _signup_and_login(client, "intelcallcoheredown@example.com", "Intel Call Cohere Down Co")
    call = _make_recorded_call(db_session, account_id)
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr("app.intelligence.service.transcribe_audio", lambda audio_bytes: "test transcript")
    monkeypatch.setattr(
        "app.intelligence.service.extract_conversation_summary",
        lambda transcript: {
            "summary": "Test summary.", "language": "en", "urgency": "low",
            "action_items": [], "suggested_follow_up": None,
        },
    )

    def _raise(*args, **kwargs):
        raise EmbeddingError("Cohere embedding request failed: connection refused")

    monkeypatch.setattr("app.intelligence.service.generate_embedding", _raise)

    response = client.post(
        f"/intelligence/calls/{call.id}/summarize", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 201, response.text
    assert response.json()["summary"] == "Test summary."


def test_search_summaries_returns_empty_when_cohere_is_unreachable(client, db_session, monkeypatch):
    """Same degrade-to-empty-results contract as an unrelated search query -
    a Cohere outage during search must read as "no matches," not an error."""
    from app.integrations.embeddings.cohere import EmbeddingError

    token, account_id = _signup_and_login(client, "searchcoheredown@example.com", "Search Cohere Down Co")
    _seed_summary(
        db_session, account_id,
        summary="Caller reported a billing dispute over their last invoice.",
        transcript="Hi, I think I was overcharged on my last invoice.",
        with_embedding=False,
    )

    def _raise(*args, **kwargs):
        raise EmbeddingError("Cohere embedding request failed: connection refused")

    monkeypatch.setattr("app.intelligence.service.generate_embedding", _raise)

    response = client.get(
        "/intelligence/summaries/search", params={"q": "billing invoice"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_summarize_video_session_returns_a_clean_502_when_storage_download_fails(client, db_session, monkeypatch):
    """Chaos test for the S3/boto3 call site in _download_and_transcribe_video
    - a genuine mid-call StorageError (bucket unreachable, object missing),
    not a "not configured" scenario."""
    from app.integrations.storage.s3 import StorageError

    token, account_id = _signup_and_login(client, "intelvidstoragedown@example.com", "Intel Video Storage Down Co")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    session = _make_video_session(db_session, account_id, me["id"], room_name="zl-test-storage-down")
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    def _raise(*args, **kwargs):
        raise StorageError("Unable to download s3://recordings/zl-test-storage-down.mp4: connection reset")

    monkeypatch.setattr("app.intelligence.service.download_object", _raise)

    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 502


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
    monkeypatch.setattr("app.intelligence.service._extract_audio", lambda video_bytes: b"fake-audio-bytes")
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


def test_summarize_video_session_uses_the_real_stored_object_key(client, db_session, monkeypatch):
    """Regression test for a real bug: recordings moved to human-readable
    S3 keys (account name + date/day/time, see media.service.
    start_video_recording) but this call site kept guessing
    "recordings/{room_name}.mp4" - which stopped being the real key and
    started 404ing (NoSuchKey) for every recording made since. Must use
    session.recording_object_key when it's set, not room_name."""
    token, account_id = _signup_and_login(client, "intelvidobjectkey@example.com", "Intel Video Key Co")
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    session = _make_video_session(db_session, account_id, me["id"], room_name="zl-test-objectkey")
    session.recording_object_key = "recordings/intel-video-key-co-2026-08-21-Friday-11-00-00-deadbeef.mp4"
    db_session.commit()

    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"}
    )

    captured = {}

    def _capture_download(key):
        captured["key"] = key
        return b"fake-video-bytes"

    monkeypatch.setattr("app.intelligence.service.download_object", _capture_download)
    monkeypatch.setattr("app.intelligence.service._extract_audio", lambda video_bytes: b"fake-audio-bytes")
    monkeypatch.setattr(
        "app.intelligence.service.transcribe_audio",
        lambda audio_bytes, filename=None, content_type=None: "A real transcript.",
    )

    response = client.post(
        f"/intelligence/video-sessions/{session.room_name}/summarize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 201, response.text
    assert captured["key"] == "recordings/intel-video-key-co-2026-08-21-Friday-11-00-00-deadbeef.mp4"


@pytest.mark.live
def test_extract_audio_shrinks_a_real_video_to_a_small_audio_file():
    """Regression test for a real incident: sending a raw video recording
    straight to Groq's transcription endpoint hit their 25MB free-tier
    upload cap on anything longer than a few minutes, and Groq dropped the
    connection instead of returning a clean error. Audio-only shrinks the
    same content by roughly two orders of magnitude. Uses a real ffmpeg-
    generated synthetic clip (silent audio + a color test pattern), not a
    mock - this is exercising the real subprocess call, not the app's own
    logic around it."""
    import subprocess

    from app.core.config import settings
    from app.intelligence.service import _extract_audio

    video_bytes = subprocess.run(
        [
            settings.ffmpeg_path, "-y",
            "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=10",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-f", "mp4", "-movflags", "frag_keyframe+empty_moov", "pipe:1",
        ],
        capture_output=True, timeout=30, check=True,
    ).stdout

    audio_bytes = _extract_audio(video_bytes)
    assert len(audio_bytes) > 0
    assert len(audio_bytes) < len(video_bytes)


def test_extract_audio_raises_a_clean_error_on_input_that_is_not_a_real_video():
    """ffmpeg itself must fail on garbage input - this must surface as our
    own AudioExtractionError, not an uncaught CalledProcessError."""
    from app.intelligence.service import AudioExtractionError, _extract_audio

    with pytest.raises(AudioExtractionError):
        _extract_audio(b"this is not a real video file")


def test_extract_audio_raises_a_clean_error_when_ffmpeg_is_missing(monkeypatch):
    """Same posture as every other Provider Gateway "not configured"
    check - a missing binary must fail cleanly, not with a raw
    FileNotFoundError bubbling out of subprocess.run."""
    from app.intelligence.service import AudioExtractionError, _extract_audio

    monkeypatch.setattr("app.intelligence.service.settings.ffmpeg_path", "ffmpeg-does-not-exist-anywhere")

    with pytest.raises(AudioExtractionError, match="not installed"):
        _extract_audio(b"irrelevant")


def _seed_summary(
    db_session, account_id: str, *, summary: str, transcript: str, with_embedding: bool = True
) -> None:
    from app.integrations.embeddings.cohere import generate_embedding
    from app.intelligence.models import ConversationSummary, SummarySourceType

    record = ConversationSummary(
        account_id=account_id, source_type=SummarySourceType.VOICEMAIL, source_id=account_id,
        transcript=transcript, summary=summary, model_version="groq/test",
    )
    if with_embedding:
        record.embedding = generate_embedding(f"{summary} {transcript}", input_type="search_document")
    db_session.add(record)
    db_session.commit()


@pytest.mark.live
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


@pytest.mark.live
def test_search_summaries_finds_a_match_by_meaning_not_exact_words(client, db_session):
    """The actual point of upgrading from keyword to semantic search - the
    seeded summary never uses the words "connectivity" or "issue", but real
    Cohere embeddings still recognize they mean the same thing. Calibrated
    live against real cosine-distance measurements (see
    _SEMANTIC_DISTANCE_THRESHOLD's docstring in intelligence/service.py)."""
    token, account_id = _signup_and_login(client, "search5@example.com", "Search Test Co 5")
    _seed_summary(
        db_session, account_id,
        summary="The internet is down and customer cannot get online.",
        transcript="My wifi isn't working and I can't connect to anything.",
    )

    response = client.get(
        "/intelligence/summaries/search", params={"q": "connectivity issue"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert "internet" in results[0]["summary"].lower()


@pytest.mark.live
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


@pytest.mark.live
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
    _seed_summary(db_session, account_id, summary="Anything", transcript="Anything at all", with_embedding=False)

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


# --- AIJob retry lineage / failure observability ---


def test_successful_summarize_creates_a_succeeded_ai_job(client, db_session):
    from app.intelligence.models import AIJob, AIJobStatus, SummarySourceType

    token, account_id = _signup_and_login(client, "aijobsuccess1@example.com", "AI Job Success Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550005556")
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"},
    )

    from unittest.mock import patch as _patch
    with _patch("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes"), \
         _patch("app.intelligence.service.transcribe_audio", lambda audio_bytes: "Test voicemail content."):
        response = client.post(
            f"/intelligence/voicemails/{voicemail.id}/summarize", headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 201
    summary_id = response.json()["id"]

    job = (
        db_session.query(AIJob)
        .filter(AIJob.source_type == SummarySourceType.VOICEMAIL, AIJob.source_id == voicemail.id)
        .first()
    )
    assert job is not None
    assert job.status == AIJobStatus.SUCCEEDED
    assert job.attempt_count == 1
    assert job.last_error is None
    assert job.conversation_summary_id == summary_id


def test_failed_transcription_records_a_failed_ai_job_and_still_raises(client, db_session, monkeypatch):
    from app.intelligence.models import AIJob, AIJobStatus, SummarySourceType

    token, account_id = _signup_and_login(client, "aijobfail1@example.com", "AI Job Fail Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550005557")
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"},
    )

    def _boom(url):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr("app.intelligence.service.download_recording", _boom)

    with pytest.raises(RuntimeError, match="simulated storage failure"):
        client.post(f"/intelligence/voicemails/{voicemail.id}/summarize", headers={"Authorization": f"Bearer {token}"})

    job = (
        db_session.query(AIJob)
        .filter(AIJob.source_type == SummarySourceType.VOICEMAIL, AIJob.source_id == voicemail.id)
        .first()
    )
    assert job is not None
    assert job.status == AIJobStatus.FAILED
    assert job.attempt_count == 1
    assert "simulated storage failure" in job.last_error
    assert job.conversation_summary_id is None


def test_retry_after_failure_increments_the_same_job_and_can_succeed(client, db_session, monkeypatch):
    from app.intelligence.models import AIJob, AIJobStatus, SummarySourceType

    token, account_id = _signup_and_login(client, "aijobretry1@example.com", "AI Job Retry Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550005558")
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"},
    )

    def _boom(url):
        raise RuntimeError("transient failure")

    monkeypatch.setattr("app.intelligence.service.download_recording", _boom)
    with pytest.raises(RuntimeError):
        client.post(f"/intelligence/voicemails/{voicemail.id}/summarize", headers={"Authorization": f"Bearer {token}"})

    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr("app.intelligence.service.transcribe_audio", lambda audio_bytes: "Recovered on retry.")
    second = client.post(
        f"/intelligence/voicemails/{voicemail.id}/summarize", headers={"Authorization": f"Bearer {token}"},
    )
    assert second.status_code == 201

    jobs = (
        db_session.query(AIJob)
        .filter(AIJob.source_type == SummarySourceType.VOICEMAIL, AIJob.source_id == voicemail.id)
        .all()
    )
    assert len(jobs) == 1  # same row updated in place, not a new one per attempt
    assert jobs[0].attempt_count == 2
    assert jobs[0].status == AIJobStatus.SUCCEEDED
    assert jobs[0].last_error is None
    assert jobs[0].conversation_summary_id is not None


def test_staff_can_list_ai_jobs_filtered_by_status(client, db_session, monkeypatch):
    from app.staff import service as staff_service
    from app.staff.models import PlatformStaffRole

    token, account_id = _signup_and_login(client, "aijobstafflist1@example.com", "AI Job Staff List Co")
    voicemail = _make_voicemail(db_session, account_id, "+15550005559")
    client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers={"Authorization": f"Bearer {token}"},
    )

    def _boom(url):
        raise RuntimeError("simulated failure for staff listing test")

    monkeypatch.setattr("app.intelligence.service.download_recording", _boom)
    with pytest.raises(RuntimeError):
        client.post(f"/intelligence/voicemails/{voicemail.id}/summarize", headers={"Authorization": f"Bearer {token}"})

    staff_service.create_staff(
        db_session, email="aijobstaff1@zoikolocal.com", password="staffpass123", role=PlatformStaffRole.SUPPORT
    )
    staff_token = client.post(
        "/staff/login", json={"email": "aijobstaff1@zoikolocal.com", "password": "staffpass123"}
    ).json()["access_token"]

    response = client.get(
        "/intelligence/jobs", params={"job_status": "failed"}, headers={"Authorization": f"Bearer {staff_token}"},
    )
    assert response.status_code == 200
    matching = [j for j in response.json() if j["source_id"] == voicemail.id]
    assert len(matching) == 1
    assert matching[0]["status"] == "failed"
    assert "simulated failure for staff listing test" in matching[0]["last_error"]
