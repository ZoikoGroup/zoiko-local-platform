import base64
import hashlib

import pytest
from google.protobuf.json_format import MessageToJson
from livekit import api as livekit_api
from livekit.protocol import egress as egress_pb
from livekit.protocol import models as models_pb
from livekit.protocol import webhook as webhook_pb

from app.core.config import settings


def _livekit_webhook_body_and_token(event: str, room_name: str) -> tuple[bytes, str]:
    body = MessageToJson(webhook_pb.WebhookEvent(event=event, room=models_pb.Room(name=room_name))).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_sha256(digest)
        .to_jwt()
    )
    return body, token


def _participant_webhook_body_and_token(event: str, room_name: str, identity: str) -> tuple[bytes, str]:
    body = MessageToJson(
        webhook_pb.WebhookEvent(
            event=event, room=models_pb.Room(name=room_name), participant=models_pb.ParticipantInfo(identity=identity)
        )
    ).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_sha256(digest)
        .to_jwt()
    )
    return body, token


def _egress_ended_webhook_body_and_token(egress_id: str, room_name: str, file_location: str) -> tuple[bytes, str]:
    egress_info = egress_pb.EgressInfo(
        egress_id=egress_id,
        room_name=room_name,
        status=egress_pb.EgressStatus.EGRESS_COMPLETE,
        file_results=[egress_pb.FileInfo(location=file_location)],
    )
    body = MessageToJson(webhook_pb.WebhookEvent(event="egress_ended", egress_info=egress_info)).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_sha256(digest)
        .to_jwt()
    )
    return body, token


def _egress_ended_webhook_body_and_token_legacy_file_field(
    egress_id: str, room_name: str, file_location: str
) -> tuple[bytes, str]:
    """Some LiveKit server versions populate only the deprecated singular
    `file` field (not the repeated `file_results` list) for a single-output
    RoomComposite egress - confirmed against a real deployment where two
    completed recordings never got a recording_url because only
    `file_results` was checked."""
    egress_info = egress_pb.EgressInfo(
        egress_id=egress_id,
        room_name=room_name,
        status=egress_pb.EgressStatus.EGRESS_COMPLETE,
        file=egress_pb.FileInfo(location=file_location),
    )
    body = MessageToJson(webhook_pb.WebhookEvent(event="egress_ended", egress_info=egress_info)).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_sha256(digest)
        .to_jwt()
    )
    return body, token


def _egress_ended_failed_webhook_body_and_token(egress_id: str, room_name: str) -> tuple[bytes, str]:
    """A genuinely failed egress - no file_results, no file - matching a
    real LiveKit failure (S3 error, egress worker crash), not a success."""
    egress_info = egress_pb.EgressInfo(
        egress_id=egress_id, room_name=room_name, status=egress_pb.EgressStatus.EGRESS_FAILED,
        error="simulated failure",
    )
    body = MessageToJson(webhook_pb.WebhookEvent(event="egress_ended", egress_info=egress_info)).encode()
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode()
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_sha256(digest)
        .to_jwt()
    )
    return body, token


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={
            "account_name": "Video Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"]


def test_create_room_requires_auth(client):
    response = client.post("/media/video/rooms")
    assert response.status_code == 401


def test_create_room_returns_a_clean_502_on_a_genuine_livekit_failure(client, db_session, monkeypatch):
    """Chaos test: LiveKit configured correctly but the API call itself
    fails (real outage/timeout - a VideoError from a genuine TwirpError,
    not a missing-config ValueError). No VideoSession row should be left
    behind half-created."""
    from app.integrations.video.livekit import VideoError
    from app.media.models import VideoSession

    async def _raise(*args, **kwargs):
        raise VideoError("livekit request failed: 503 Service Unavailable")

    monkeypatch.setattr("app.media.service.video.create_room", _raise)
    token = _signup_and_login(client, "videocreatelivekitdown@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 502

    assert db_session.query(VideoSession).filter(VideoSession.account_id == account_id).count() == 0


def test_end_room_returns_a_clean_502_on_a_genuine_livekit_failure(client, db_session, monkeypatch):
    """Same chaos scenario for ending a room - the session must stay ACTIVE
    (not silently marked ENDED) if the provider call actually failed, since
    that would desync our record from the still-live LiveKit room."""
    from app.integrations.video.livekit import VideoError
    from app.media.models import VideoSession, VideoSessionStatus

    token = _signup_and_login(client, "videoendlivekitdown@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    session = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name="zl-test-end-livekit-down",
        status=VideoSessionStatus.ACTIVE,
    )
    db_session.add(session)
    db_session.commit()

    async def _raise(*args, **kwargs):
        raise VideoError("livekit request failed: 503 Service Unavailable")

    monkeypatch.setattr("app.media.service.video.end_room", _raise)

    response = client.post(f"/media/video/rooms/{session.room_name}/end", headers=headers)
    assert response.status_code == 502

    db_session.refresh(session)
    assert session.status == VideoSessionStatus.ACTIVE
    assert session.ended_at is None


def test_start_recording_returns_a_clean_502_on_a_genuine_livekit_failure(client, db_session, monkeypatch):
    """Chaos test for the Egress API call itself failing - the session must
    not be left thinking a recording is in progress (recording_egress_id
    must stay unset) if LiveKit never actually started one."""
    from app.integrations.video.livekit import VideoError
    from app.media.models import VideoSession, VideoSessionStatus

    token = _signup_and_login(client, "videorecordlivekitdown@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    me = client.get("/auth/me", headers=headers).json()

    session = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name="zl-test-record-livekit-down",
        status=VideoSessionStatus.ACTIVE,
    )
    db_session.add(session)
    db_session.commit()

    async def _raise(*args, **kwargs):
        raise VideoError("livekit egress request failed: 503 Service Unavailable")

    monkeypatch.setattr("app.media.service.video.start_room_recording", _raise)

    response = client.post(f"/media/video/rooms/{session.room_name}/recording/start", headers=headers)
    assert response.status_code == 502

    db_session.refresh(session)
    assert session.recording_egress_id is None


def test_list_rooms_degrades_recording_url_to_none_on_a_genuine_s3_failure(client, db_session, monkeypatch):
    """Chaos test for the S3/boto3 call site in get_recording_download_url -
    a genuine presigned-URL generation failure (bucket unreachable) must not
    break the whole call-history list, just that one row's download link."""
    from app.integrations.storage.s3 import StorageError
    from app.media.models import VideoSession, VideoSessionStatus

    token = _signup_and_login(client, "videolists3down@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    session = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name="zl-test-list-s3-down",
        status=VideoSessionStatus.ENDED, recording_url="https://s3.example.com/recordings/zl-test-list-s3-down.mp4",
    )
    db_session.add(session)
    db_session.commit()

    def _raise(*args, **kwargs):
        raise StorageError("Unable to generate presigned URL: connection reset")

    monkeypatch.setattr("app.media.service.generate_presigned_url", _raise)

    response = client.get("/media/video/rooms", headers=headers)
    assert response.status_code == 200
    matching = next(r for r in response.json() if r["room_name"] == "zl-test-list-s3-down")
    assert matching["recording_url"] is None


def test_create_room_fails_cleanly_when_livekit_url_is_not_configured(client, monkeypatch):
    """A missing LIVEKIT_URL must raise a clean 502, not an unhandled 500 -
    and not silently succeed either. _client() checks this explicitly
    rather than trusting the SDK's own constructor: LiveKitAPI falls back
    to os.getenv("LIVEKIT_URL") whenever a falsy url is passed, which would
    otherwise silently resurrect the real env var's value and mask this
    exact "not configured" scenario - confirmed live, that used to make
    this test's monkeypatch a no-op (room creation actually succeeded)."""
    monkeypatch.setattr("app.integrations.video.livekit.settings.livekit_url", "")
    token = _signup_and_login(client, "videonolivekit@example.com")

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 502
    assert "LiveKit is not configured" in response.json()["detail"]


@pytest.mark.live
def test_video_room_lifecycle(client):
    token = _signup_and_login(client, "videouser@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post("/media/video/rooms", headers=headers)
    assert create_response.status_code == 201
    room = create_response.json()
    assert room["status"] == "active"
    room_name = room["room_name"]

    token_response = client.post(
        f"/media/video/rooms/{room_name}/token", json={"display_name": "Test User"}, headers=headers
    )
    assert token_response.status_code == 200
    assert token_response.json()["token"]

    list_response = client.get("/media/video/rooms", headers=headers)
    assert list_response.status_code == 200
    assert any(r["room_name"] == room_name for r in list_response.json())

    end_response = client.post(f"/media/video/rooms/{room_name}/end", headers=headers)
    assert end_response.status_code == 200
    assert end_response.json()["status"] == "ended"


@pytest.mark.live
def test_end_room_rejects_other_account(client):
    token1 = _signup_and_login(client, "videohost@example.com")
    token2 = _signup_and_login(client, "videointruder@example.com")

    create_response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token1}"})
    room_name = create_response.json()["room_name"]

    end_response = client.post(
        f"/media/video/rooms/{room_name}/end", headers={"Authorization": f"Bearer {token2}"}
    )
    assert end_response.status_code == 403

    # cleanup: end the room with the correct account so it's not left dangling on LiveKit
    client.post(f"/media/video/rooms/{room_name}/end", headers={"Authorization": f"Bearer {token1}"})


def test_start_recording_requires_auth(client):
    response = client.post("/media/video/rooms/some-room/recording/start")
    assert response.status_code == 401


@pytest.mark.live
def test_start_recording_requires_consent(client):
    token = _signup_and_login(client, "videorecordnoconsent@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    response = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_start_recording_fails_cleanly_when_storage_is_not_configured(client, monkeypatch):
    """Consent granted, but no S3-compatible bucket configured - LiveKit's
    Egress API has no free built-in storage, so this must fail cleanly (502)
    rather than crash, same as the other missing-credential cases."""
    monkeypatch.setattr("app.integrations.video.livekit.settings.s3_bucket", "")
    token = _signup_and_login(client, "videorecordnostorage@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    response = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert response.status_code == 502
    assert "storage is not configured" in response.json()["detail"].lower()

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


async def _fake_start_recording(room_name, object_key):
    return "EG_fake_egress_id"


def _fake_stop_recording(sink: list | None = None):
    async def _stop(egress_id):
        if sink is not None:
            sink.append(egress_id)

    return _stop


@pytest.mark.live
def test_member_cannot_start_recording_on_a_room_they_did_not_host(client, db_session, monkeypatch):
    """Same host-only restriction as ending a room (Member scoping) -
    recording is the more sensitive of the two actions, so it must not be
    open to any account Member regardless of who hosted the call."""
    monkeypatch.setattr("app.media.service.video.start_room_recording", _fake_start_recording)
    monkeypatch.setattr("app.media.service.video.stop_room_recording", _fake_stop_recording())

    from app.billing import service as billing_service

    owner_token = _signup_and_login(client, "videorecordhostowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    owner_account_id = client.get("/auth/me", headers=owner_headers).json()["account_id"]
    # team.members.enabled is Business+ (ZL-COM-ENT-001) - a fresh signup's
    # default free_trial plan grants no team capability.
    billing_service.change_plan(db_session, owner_account_id, "business", actor="test-setup")
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=owner_headers)
    room_name = client.post("/media/video/rooms", headers=owner_headers).json()["room_name"]

    client.post(
        "/team/members",
        json={"email": "videorecordhostmember@example.com", "password": "supersecret123", "role": "member"},
        headers=owner_headers,
    )
    member_token = client.post(
        "/auth/login", json={"email": "videorecordhostmember@example.com", "password": "supersecret123"}
    ).json()["access_token"]

    response = client.post(
        f"/media/video/rooms/{room_name}/recording/start",
        headers={"Authorization": f"Bearer {member_token}"},
    )
    assert response.status_code == 403
    assert "not started by you" in response.json()["detail"].lower()

    client.post(f"/media/video/rooms/{room_name}/end", headers=owner_headers)


@pytest.mark.live
def test_start_recording_succeeds_with_consent(client, monkeypatch):
    monkeypatch.setattr("app.media.service.video.start_room_recording", _fake_start_recording)
    monkeypatch.setattr("app.media.service.video.stop_room_recording", _fake_stop_recording())
    token = _signup_and_login(client, "videorecordconsent@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    response = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["recording"] is True

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["recording_in_progress"] is True

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_start_recording_builds_a_human_readable_object_key(client, db_session, monkeypatch):
    """Regression test: recordings used to be stored under the internal
    random room_name ("zl-<uuid hex>.mp4") - meaningless to a human browsing
    the bucket. The real key must be built from the account's name and the
    real start date/day/time instead, with LiveKit's start call receiving
    that same key (not deriving its own)."""
    import re

    captured = {}

    async def _capture_start_recording(room_name, object_key):
        captured["object_key"] = object_key
        return "EG_keytest_egress_id"

    monkeypatch.setattr("app.media.service.video.start_room_recording", _capture_start_recording)
    monkeypatch.setattr("app.media.service.video.stop_room_recording", _fake_stop_recording())

    token = _signup_and_login(client, "videorecordobjectkey@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    response = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert response.status_code == 200, response.text

    # "video-test-co" from the real signup account name "Video Test Co",
    # then a real ISO date, full weekday name, and 24h time - not the raw
    # room_name anywhere in it.
    assert re.match(
        r"^recordings/video-test-co-\d{4}-\d{2}-\d{2}-[A-Za-z]+-\d{2}-\d{2}-\d{2}-[0-9a-f]{8}\.mp4$",
        captured["object_key"],
    ), captured["object_key"]
    assert room_name not in captured["object_key"]

    from app.media.models import VideoSession

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    assert session.recording_object_key == captured["object_key"]
    assert session.recording_started_at is not None

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_start_recording_rejects_when_already_recording(client, monkeypatch):
    monkeypatch.setattr("app.media.service.video.start_room_recording", _fake_start_recording)
    monkeypatch.setattr("app.media.service.video.stop_room_recording", _fake_stop_recording())
    token = _signup_and_login(client, "videorecordtwice@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    first = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert first.status_code == 200
    second = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert second.status_code == 403
    assert "already being recorded" in second.json()["detail"]

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_end_session_stops_an_in_progress_recording(client, monkeypatch):
    monkeypatch.setattr("app.media.service.video.start_room_recording", _fake_start_recording)
    stopped = []
    monkeypatch.setattr("app.media.service.video.stop_room_recording", _fake_stop_recording(stopped))
    token = _signup_and_login(client, "videorecordend@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]
    client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)

    end_response = client.post(f"/media/video/rooms/{room_name}/end", headers=headers)
    assert end_response.status_code == 200
    assert stopped == ["EG_fake_egress_id"]


@pytest.mark.live
def test_webhook_egress_ended_attaches_recording_url(client, db_session):
    token = _signup_and_login(client, "videorecordwebhook@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    from app.media.models import VideoSession

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_real_test_id"
    db_session.commit()

    body, auth_token = _egress_ended_webhook_body_and_token(
        "EG_real_test_id", room_name, "https://example.com/recordings/fake.mp4"
    )
    webhook_response = client.post(
        "/media/video/webhook", content=body, headers={"Authorization": auth_token}
    )
    assert webhook_response.status_code == 204

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    # The bucket is private, so what's served is a freshly generated
    # presigned download URL keyed off room_name, not the literal webhook
    # value - see get_recording_download_url.
    assert matching["recording_url"].startswith(
        f"https://s3.us-east-005.backblazeb2.com/zoiko-local-video-recordings/recordings/{room_name}.mp4"
    )
    assert "X-Amz-Signature" in matching["recording_url"]
    assert matching["recording_in_progress"] is False

    db_session.refresh(session)
    assert session.recording_url == "https://example.com/recordings/fake.mp4"

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_webhook_egress_ended_attaches_recording_url_from_legacy_file_field(client, db_session):
    token = _signup_and_login(client, "videorecordwebhooklegacy@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    from app.media.models import VideoSession

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_legacy_test_id"
    db_session.commit()

    body, auth_token = _egress_ended_webhook_body_and_token_legacy_file_field(
        "EG_legacy_test_id", room_name, "https://example.com/recordings/legacy.mp4"
    )
    webhook_response = client.post(
        "/media/video/webhook", content=body, headers={"Authorization": auth_token}
    )
    assert webhook_response.status_code == 204

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["recording_url"].startswith(
        f"https://s3.us-east-005.backblazeb2.com/zoiko-local-video-recordings/recordings/{room_name}.mp4"
    )
    assert "X-Amz-Signature" in matching["recording_url"]
    assert matching["recording_in_progress"] is False

    db_session.refresh(session)
    assert session.recording_url == "https://example.com/recordings/legacy.mp4"

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


def test_webhook_egress_ended_failure_clears_stuck_recording_state(client, db_session):
    """Regression test for a real bug: a failed egress used to leave
    recording_egress_id set with recording_url still None forever -
    is_recording_in_progress stayed True permanently, blocking a new
    recording with a wrong "already being recorded" error and showing
    "Recording processing..." in the UI with no way to tell the customer
    anything went wrong. The failure webhook must resolve both."""
    from app.media.models import VideoSession
    from app.media.service import RECORDING_FAILED_MARKER

    token = _signup_and_login(client, "videorecordfailedwebhook@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_failed_test_id"
    db_session.commit()

    body, auth_token = _egress_ended_failed_webhook_body_and_token("EG_failed_test_id", room_name)
    webhook_response = client.post(
        "/media/video/webhook", content=body, headers={"Authorization": auth_token}
    )
    assert webhook_response.status_code == 204

    db_session.refresh(session)
    assert session.recording_egress_id is None
    assert session.recording_url == RECORDING_FAILED_MARKER

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["recording_in_progress"] is False
    assert matching["recording_failed"] is True
    assert matching["recording_url"] is None  # never served as a real download link

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


def test_sweep_stale_video_recordings_clears_a_lost_webhook_with_no_recoverable_file(
    client, db_session, monkeypatch,
):
    """The other half of the same bug: the egress_ended webhook is never
    guaranteed to arrive at all (LiveKit outage, dropped delivery) - nothing
    else would ever clear a recording stuck this way without the sweep.
    LiveKit itself has no record of this egress_id, so there's genuinely
    nothing to recover - only then is it safe to mark it failed."""
    from datetime import datetime, timedelta, timezone

    from app.media.models import VideoSession
    from app.media.service import RECORDING_FAILED_MARKER, sweep_stale_video_recordings

    async def _fake_get_egress_status(egress_id):
        return None

    monkeypatch.setattr("app.media.service.video.get_egress_status", _fake_get_egress_status)

    token = _signup_and_login(client, "videorecordstalesweep@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_stale_test_id"
    session.recording_started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.commit()

    result = sweep_stale_video_recordings(db_session)
    assert result["swept"] >= 1

    db_session.refresh(session)
    assert session.recording_egress_id is None
    assert session.recording_url == RECORDING_FAILED_MARKER


def test_sweep_stale_video_recordings_recovers_a_lost_webhook_that_actually_succeeded(
    client, db_session, monkeypatch,
):
    """Regression test for a real incident: a lost webhook does NOT mean
    the recording failed - LiveKit can report EGRESS_COMPLETE with a real
    uploaded file even though our backend never heard about it (confirmed
    live, 2026-08-21, caused by our own backend restarting mid-delivery).
    The sweep must recover the real file, not blindly mark it failed."""
    from datetime import datetime, timedelta, timezone

    from app.media.models import VideoSession
    from app.media.service import sweep_stale_video_recordings

    async def _fake_get_egress_status(egress_id):
        return {"status": 3, "location": "https://example.com/recordings/recovered.mp4", "error": ""}

    monkeypatch.setattr("app.media.service.video.get_egress_status", _fake_get_egress_status)

    token = _signup_and_login(client, "videorecordstalesweeprecover@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_recoverable_test_id"
    session.recording_started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.commit()

    result = sweep_stale_video_recordings(db_session)
    assert result["swept"] >= 1

    db_session.refresh(session)
    assert session.recording_egress_id is None
    assert session.recording_url == "https://example.com/recordings/recovered.mp4"


def test_sweep_stale_video_recordings_does_not_touch_a_recent_in_progress_recording(client, db_session):
    """A recording that started 5 minutes ago is still legitimately in
    progress - the sweep must not treat every in-progress recording as
    stale, only ones well past any real call length."""
    from datetime import datetime, timedelta, timezone

    from app.media.models import VideoSession
    from app.media.service import sweep_stale_video_recordings

    token = _signup_and_login(client, "videorecordfreshinprogress@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_fresh_test_id"
    session.recording_started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.commit()

    sweep_stale_video_recordings(db_session)

    db_session.refresh(session)
    assert session.recording_egress_id == "EG_fresh_test_id"  # untouched
    assert session.recording_url is None


def test_sweep_stale_video_recordings_leaves_a_genuinely_long_running_egress_alone(
    client, db_session, monkeypatch,
):
    """Past the timeout is only a hint something might be wrong - if LiveKit
    itself still reports the egress as STARTING/ACTIVE/ENDING, it's a real,
    unusually long call, not a lost webhook. Must not be interrupted."""
    from datetime import datetime, timedelta, timezone

    from app.media.models import VideoSession
    from app.media.service import sweep_stale_video_recordings

    async def _fake_get_egress_status(egress_id):
        return {"status": 1, "location": None, "error": ""}  # EGRESS_ACTIVE

    monkeypatch.setattr("app.media.service.video.get_egress_status", _fake_get_egress_status)

    token = _signup_and_login(client, "videorecordstillactivesweep@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    session = db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first()
    session.recording_egress_id = "EG_still_active_test_id"
    session.recording_started_at = datetime.now(timezone.utc) - timedelta(hours=3)
    db_session.commit()

    sweep_stale_video_recordings(db_session)

    db_session.refresh(session)
    assert session.recording_egress_id == "EG_still_active_test_id"  # untouched
    assert session.recording_url is None


@pytest.mark.live
def test_webhook_rejects_invalid_signature(client):
    body, _ = _livekit_webhook_body_and_token("room_finished", "some-room")
    response = client.post(
        "/media/video/webhook", content=body, headers={"Authorization": "not-a-real-token"}
    )
    assert response.status_code == 403


@pytest.mark.live
def test_webhook_room_finished_marks_session_ended(client):
    token = _signup_and_login(client, "videowebhookuser@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    create_response = client.post("/media/video/rooms", headers=headers)
    room_name = create_response.json()["room_name"]

    body, auth_token = _livekit_webhook_body_and_token("room_finished", room_name)
    webhook_response = client.post(
        "/media/video/webhook", content=body, headers={"Authorization": auth_token}
    )
    assert webhook_response.status_code == 204

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = [r for r in list_response.json() if r["room_name"] == room_name]
    assert len(matching) == 1
    assert matching[0]["status"] == "ended"

    # cleanup on the LiveKit side (DB already reflects "ended" via the webhook,
    # but the real room object may still exist until LiveKit's own timeout)
    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_participant_joined_creates_an_open_participant_session(client, db_session):
    from app.media.models import VideoParticipantSession

    token = _signup_and_login(client, "videoparticipant1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    body, auth_token = _participant_webhook_body_and_token("participant_joined", room_name, "user-abc")
    response = client.post("/media/video/webhook", content=body, headers={"Authorization": auth_token})
    assert response.status_code == 204

    row = (
        db_session.query(VideoParticipantSession)
        .filter(VideoParticipantSession.participant_identity == "user-abc")
        .first()
    )
    assert row is not None
    assert row.joined_at is not None
    assert row.left_at is None

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_participant_left_closes_the_matching_open_session(client, db_session):
    from app.media.models import VideoParticipantSession

    token = _signup_and_login(client, "videoparticipant2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    joined_body, joined_token = _participant_webhook_body_and_token("participant_joined", room_name, "user-xyz")
    client.post("/media/video/webhook", content=joined_body, headers={"Authorization": joined_token})

    left_body, left_token = _participant_webhook_body_and_token("participant_left", room_name, "user-xyz")
    response = client.post("/media/video/webhook", content=left_body, headers={"Authorization": left_token})
    assert response.status_code == 204

    row = (
        db_session.query(VideoParticipantSession)
        .filter(VideoParticipantSession.participant_identity == "user-xyz")
        .first()
    )
    assert row.left_at is not None

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["participant_minutes"] >= 0

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_room_finished_closes_any_dangling_open_participant_sessions(client, db_session):
    from app.media.models import VideoParticipantSession

    token = _signup_and_login(client, "videoparticipant3@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    joined_body, joined_token = _participant_webhook_body_and_token("participant_joined", room_name, "user-nolev")
    client.post("/media/video/webhook", content=joined_body, headers={"Authorization": joined_token})

    # no participant_left ever arrives (abrupt disconnect) - room_finished
    # must still close the row out, not leave it open forever
    finished_body, finished_token = _livekit_webhook_body_and_token("room_finished", room_name)
    client.post("/media/video/webhook", content=finished_body, headers={"Authorization": finished_token})

    row = (
        db_session.query(VideoParticipantSession)
        .filter(VideoParticipantSession.participant_identity == "user-nolev")
        .first()
    )
    assert row.left_at is not None

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


def test_get_participant_minutes_sums_every_participants_time(db_session):
    from datetime import datetime, timedelta, timezone

    from app.media.models import VideoParticipantSession, VideoSession, VideoSessionStatus
    from app.media.service import get_participant_minutes
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    account = Account(name="Usage Unit Test Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    user = User(account_id=account.id, email="usageunit@example.com", role=UserRole.OWNER)
    db_session.add(user)
    db_session.flush()

    session = VideoSession(
        account_id=account.id, host_user_id=user.id, room_name="zl-usagetest1", status=VideoSessionStatus.ENDED
    )
    db_session.add(session)
    db_session.flush()

    base = datetime.now(timezone.utc)
    db_session.add_all(
        [
            VideoParticipantSession(
                video_session_id=session.id, participant_identity="a",
                joined_at=base, left_at=base + timedelta(minutes=10),
            ),
            VideoParticipantSession(
                video_session_id=session.id, participant_identity="b",
                joined_at=base, left_at=base + timedelta(minutes=4),
            ),
        ]
    )
    db_session.commit()

    assert get_participant_minutes(db_session, session.id) == 14.0


def test_usage_endpoint_sums_across_all_of_an_accounts_sessions(client, db_session):
    from datetime import datetime, timedelta, timezone

    from app.media.models import VideoParticipantSession, VideoSession, VideoSessionStatus

    token = _signup_and_login(client, "videousage1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()

    session1 = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name="zl-usageacct1",
        status=VideoSessionStatus.ENDED,
    )
    session2 = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name="zl-usageacct2",
        status=VideoSessionStatus.ENDED,
    )
    db_session.add_all([session1, session2])
    db_session.flush()

    base = datetime.now(timezone.utc)
    db_session.add_all(
        [
            VideoParticipantSession(
                video_session_id=session1.id, participant_identity="a",
                joined_at=base, left_at=base + timedelta(minutes=5),
            ),
            VideoParticipantSession(
                video_session_id=session2.id, participant_identity="a",
                joined_at=base, left_at=base + timedelta(minutes=7),
            ),
        ]
    )
    db_session.commit()

    response = client.get("/media/video/usage", headers=headers)
    assert response.status_code == 200
    assert response.json()["participant_minutes"] == 12.0


@pytest.mark.live
def test_group_video_call_issues_join_tokens_to_more_than_two_team_members(client, db_session):
    """Roadmap §8 'Video Calling - Phase 1 Standard' targets up to
    MAX_PARTICIPANTS=8 (app.integrations.video.livekit) - this only verified
    1:1 rooms live before. Confirms a real LiveKit room actually accepts join
    tokens for a 3rd and 4th distinct identity on the same room, not just a
    host and one other."""
    from app.billing import service as billing_service

    owner_token = _signup_and_login(client, "groupvideoowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    owner_account_id = client.get("/auth/me", headers=owner_headers).json()["account_id"]
    # team.members.enabled is Business+ (ZL-COM-ENT-001) - a fresh signup's
    # default free_trial plan grants no team capability.
    billing_service.change_plan(db_session, owner_account_id, "business", actor="test-setup")

    for email in ("groupvideomember1@example.com", "groupvideomember2@example.com"):
        add_response = client.post(
            "/team/members",
            json={"email": email, "password": "membersecret123", "role": "member"},
            headers=owner_headers,
        )
        assert add_response.status_code == 201, add_response.text

    member1_token = client.post(
        "/auth/login", json={"email": "groupvideomember1@example.com", "password": "membersecret123"}
    ).json()["access_token"]
    member2_token = client.post(
        "/auth/login", json={"email": "groupvideomember2@example.com", "password": "membersecret123"}
    ).json()["access_token"]

    room_name = client.post("/media/video/rooms", headers=owner_headers).json()["room_name"]

    tokens = []
    for headers, display_name in (
        (owner_headers, "Owner"),
        ({"Authorization": f"Bearer {member1_token}"}, "Member One"),
        ({"Authorization": f"Bearer {member2_token}"}, "Member Two"),
    ):
        response = client.post(
            f"/media/video/rooms/{room_name}/token", json={"display_name": display_name}, headers=headers
        )
        assert response.status_code == 200, response.text
        tokens.append(response.json()["token"])

    # Each participant gets their own distinct signed token (identity =
    # their own user id) - not the same token reused across participants.
    assert len(set(tokens)) == 3

    client.post(f"/media/video/rooms/{room_name}/end", headers=owner_headers)


@pytest.mark.live
def test_group_video_call_tracks_three_concurrent_participants(client, db_session):
    """Extends test_room_finished_closes_any_dangling_open_participant_sessions
    (which only ever exercised a single dangling participant) to 3 - proves
    usage tracking and the abrupt-disconnect cleanup both scale past a pair."""
    from app.media.models import VideoParticipantSession

    token = _signup_and_login(client, "groupvideotrack@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    identities = ["participant-a", "participant-b", "participant-c"]
    for identity in identities:
        body, auth_token = _participant_webhook_body_and_token("participant_joined", room_name, identity)
        response = client.post("/media/video/webhook", content=body, headers={"Authorization": auth_token})
        assert response.status_code == 204

    open_rows = (
        db_session.query(VideoParticipantSession)
        .filter(
            VideoParticipantSession.participant_identity.in_(identities),
            VideoParticipantSession.left_at.is_(None),
        )
        .all()
    )
    assert len(open_rows) == 3

    # One participant leaves normally; the other two are still mid-call when
    # the room ends (e.g. both disconnected without a clean leave event).
    left_body, left_token = _participant_webhook_body_and_token("participant_left", room_name, "participant-a")
    client.post("/media/video/webhook", content=left_body, headers={"Authorization": left_token})

    finished_body, finished_token = _livekit_webhook_body_and_token("room_finished", room_name)
    client.post("/media/video/webhook", content=finished_body, headers={"Authorization": finished_token})

    rows = (
        db_session.query(VideoParticipantSession)
        .filter(VideoParticipantSession.participant_identity.in_(identities))
        .all()
    )
    assert len(rows) == 3
    assert all(row.left_at is not None for row in rows)


def _seed_active_session(db_session, room_name: str, *, account_id: str | None = None, host_user_id: str | None = None):
    """Seeds a VideoSession row directly, bypassing the real LiveKit room-
    creation call - build_participant_token (what guest-token issuance
    actually calls) is pure JWT signing with no network call, so guest-join
    tests don't need @pytest.mark.live at all."""
    from app.media.models import VideoSession, VideoSessionStatus
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    if account_id is None:
        account = Account(name="Guest Join Test Co", account_type=AccountType.BUSINESS)
        db_session.add(account)
        db_session.flush()
        account_id = account.id
    if host_user_id is None:
        user = User(account_id=account_id, email=f"guesthost-{room_name}@example.com", role=UserRole.OWNER)
        db_session.add(user)
        db_session.flush()
        host_user_id = user.id

    session = VideoSession(
        account_id=account_id, host_user_id=host_user_id, room_name=room_name,
        status=VideoSessionStatus.ACTIVE,
    )
    db_session.add(session)
    db_session.commit()
    return session


def test_guest_join_requires_no_auth_and_lands_in_the_waiting_room(client, db_session):
    session = _seed_active_session(db_session, "zl-guest-test-1")

    response = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Alex Guest"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["waiting_id"]


def test_guest_join_rejects_a_nonexistent_room(client):
    response = client.post(
        "/media/video/rooms/zl-does-not-exist/guest-token", json={"display_name": "Alex Guest"}
    )
    assert response.status_code == 404


def test_guest_join_rejects_an_ended_room(client, db_session):
    from app.media.models import VideoSessionStatus

    session = _seed_active_session(db_session, "zl-guest-test-ended")
    session.status = VideoSessionStatus.ENDED
    db_session.commit()

    response = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Alex Guest"}
    )
    assert response.status_code == 404


def test_guest_join_rejects_a_blank_display_name(client, db_session):
    session = _seed_active_session(db_session, "zl-guest-test-blank-name")

    response = client.post(f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": ""})
    assert response.status_code == 422


def test_guest_join_creates_an_audit_event(client, db_session):
    from app.audit.models import AuditEvent

    session = _seed_active_session(db_session, "zl-guest-test-audit")

    response = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Audited Guest"}
    )
    assert response.status_code == 200

    events = (
        db_session.query(AuditEvent)
        .filter(AuditEvent.action == "video.guest_join_requested", AuditEvent.target == f"video_session:{session.id}")
        .all()
    )
    assert len(events) == 1


def test_guest_join_is_rate_limited_after_repeated_attempts(client, db_session):
    session = _seed_active_session(db_session, "zl-guest-test-ratelimit")

    for _ in range(10):
        response = client.post(
            f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Repeat Guest"}
        )
        assert response.status_code == 200

    over_limit_response = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Repeat Guest"}
    )
    assert over_limit_response.status_code == 429


def test_waiting_status_is_pending_before_the_host_responds(client, db_session):
    session = _seed_active_session(db_session, "zl-waiting-test-pending")
    waiting_id = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Waiting Guest"}
    ).json()["waiting_id"]

    response = client.get(f"/media/video/rooms/{session.room_name}/waiting/{waiting_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["token"] is None


def test_waiting_status_rejects_an_unknown_waiting_id(client, db_session):
    session = _seed_active_session(db_session, "zl-waiting-test-unknown")

    response = client.get(f"/media/video/rooms/{session.room_name}/waiting/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def _seed_active_session_with_real_owner(client, db_session, email: str, room_name: str):
    """Like _seed_active_session, but the host is a real signed-up account
    (with a real password) so tests can authenticate as them via the real
    /auth/login endpoint - needed for the host-side waiting-room actions,
    which require a genuine bearer token, not just a DB row."""
    from app.media.models import VideoSession, VideoSessionStatus

    token = _signup_and_login(client, email)
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()
    session = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name=room_name,
        status=VideoSessionStatus.ACTIVE,
    )
    db_session.add(session)
    db_session.commit()
    return session, {"Authorization": f"Bearer {token}"}


@pytest.mark.live
def test_host_can_list_and_admit_a_waiting_guest(client, db_session):
    from jose import jwt as jose_jwt

    session, owner_headers = _seed_active_session_with_real_owner(
        client, db_session, "waitingroomhost1@example.com", "zl-waiting-test-admit"
    )

    waiting_id = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Admit Me"}
    ).json()["waiting_id"]

    list_response = client.get(f"/media/video/rooms/{session.room_name}/waiting", headers=owner_headers)
    assert list_response.status_code == 200
    assert any(g["id"] == waiting_id and g["display_name"] == "Admit Me" for g in list_response.json())

    admit_response = client.post(
        f"/media/video/rooms/{session.room_name}/waiting/{waiting_id}/admit", headers=owner_headers
    )
    assert admit_response.status_code == 200

    # The guest's own poll (no auth) must now see a real token.
    status_response = client.get(f"/media/video/rooms/{session.room_name}/waiting/{waiting_id}")
    body = status_response.json()
    assert body["status"] == "admitted"
    assert body["token"]
    claims = jose_jwt.get_unverified_claims(body["token"])
    assert claims["sub"].startswith("guest-")

    # Admitted guests drop off the host's pending list.
    list_after = client.get(f"/media/video/rooms/{session.room_name}/waiting", headers=owner_headers)
    assert not any(g["id"] == waiting_id for g in list_after.json())


def test_host_can_deny_a_waiting_guest(client, db_session):
    session, owner_headers = _seed_active_session_with_real_owner(
        client, db_session, "waitingroomhost2@example.com", "zl-waiting-test-deny"
    )

    waiting_id = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Deny Me"}
    ).json()["waiting_id"]

    deny_response = client.post(
        f"/media/video/rooms/{session.room_name}/waiting/{waiting_id}/deny", headers=owner_headers
    )
    assert deny_response.status_code == 200

    status_response = client.get(f"/media/video/rooms/{session.room_name}/waiting/{waiting_id}")
    body = status_response.json()
    assert body["status"] == "denied"
    assert body["token"] is None


def test_create_room_uses_the_free_trial_plans_participant_cap(client, monkeypatch):
    """Confirms the account's real billing plan drives real room capacity,
    not a flat ceiling for every account (Roadmap doc §8 'Phase 1... up to
    8 participants' vs the 'larger meetings' Phase 3 tier) - free_trial/
    starter are seeded at max_video_participants=8."""
    captured = {}

    async def _capture(room_name, max_participants=None):
        captured["max_participants"] = max_participants
        return None

    monkeypatch.setattr("app.media.service.video.create_room", _capture)
    token = _signup_and_login(client, "videocaptrial@example.com")

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    assert captured["max_participants"] == 8


def test_create_room_uses_the_upgraded_plans_participant_cap(client, db_session, monkeypatch):
    """Same as above but for an account upgraded to the business plan
    (seeded at max_video_participants=25) - proves capacity actually scales
    with the plan rather than being fixed at signup."""
    from app.billing.service import change_plan

    captured = {}

    async def _capture(room_name, max_participants=None):
        captured["max_participants"] = max_participants
        return None

    monkeypatch.setattr("app.media.service.video.create_room", _capture)
    token = _signup_and_login(client, "videocapbusiness@example.com")
    account_id = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["account_id"]
    change_plan(db_session, account_id, "business", actor=account_id)

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    assert captured["max_participants"] == 25


def test_create_room_defaults_to_not_confidential(client, monkeypatch):
    monkeypatch.setattr("app.media.service.video.create_room", _fake_create_room)
    token = _signup_and_login(client, "videoconfidentialdefault@example.com")

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201
    assert response.json()["confidential"] is False


def test_create_room_persists_confidential_flag(client, db_session, monkeypatch):
    from app.media.models import VideoSession

    monkeypatch.setattr("app.media.service.video.create_room", _fake_create_room)
    token = _signup_and_login(client, "videoconfidentialcreate@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post("/media/video/rooms", json={"confidential": True}, headers=headers)
    assert response.status_code == 201
    assert response.json()["confidential"] is True

    session = db_session.query(VideoSession).filter(VideoSession.room_name == response.json()["room_name"]).first()
    assert session.confidential is True

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == response.json()["room_name"])
    assert matching["confidential"] is True


def test_start_recording_is_blocked_unconditionally_for_a_confidential_session(client, db_session):
    from app.media.models import VideoSession, VideoSessionStatus

    token = _signup_and_login(client, "videoconfidentialrecord@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers)
    me = client.get("/auth/me", headers=headers).json()

    session = VideoSession(
        account_id=me["account_id"], host_user_id=me["id"], room_name="zl-test-confidential-record",
        status=VideoSessionStatus.ACTIVE, confidential=True,
    )
    db_session.add(session)
    db_session.commit()

    # Consent is granted, so this proves confidential mode blocks recording
    # on its own - not merely as a side effect of missing consent.
    response = client.post(f"/media/video/rooms/{session.room_name}/recording/start", headers=headers)
    assert response.status_code == 403
    assert "confidential" in response.json()["detail"].lower()

    db_session.refresh(session)
    assert session.recording_egress_id is None


async def _fake_create_room(room_name, max_participants=None):
    return None


def test_a_different_accounts_host_cannot_see_or_admit_a_waiting_guest(client, db_session):
    session = _seed_active_session(db_session, "zl-waiting-test-other-account")
    waiting_id = client.post(
        f"/media/video/rooms/{session.room_name}/guest-token", json={"display_name": "Someone"}
    ).json()["waiting_id"]

    intruder_token = _signup_and_login(client, "waitingroomintruder@example.com")
    intruder_headers = {"Authorization": f"Bearer {intruder_token}"}

    list_response = client.get(f"/media/video/rooms/{session.room_name}/waiting", headers=intruder_headers)
    assert list_response.status_code == 403

    admit_response = client.post(
        f"/media/video/rooms/{session.room_name}/waiting/{waiting_id}/admit", headers=intruder_headers
    )
    assert admit_response.status_code == 403


# --- Call-quality telemetry ---


def test_report_call_quality_requires_auth(client):
    response = client.post("/media/video/rooms/some-room/quality", json={"quality": "good"})
    assert response.status_code == 401


@pytest.mark.live
def test_report_call_quality_404s_with_no_open_participant_session(client):
    token = _signup_and_login(client, "qualitynosession@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    response = client.post(
        f"/media/video/rooms/{room_name}/quality", json={"quality": "poor"}, headers=headers
    )
    assert response.status_code == 404

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


def _open_participant_session(db_session, video_session_id: str, participant_identity: str):
    from datetime import datetime, timezone

    from app.media.models import VideoParticipantSession

    row = VideoParticipantSession(
        video_session_id=video_session_id, participant_identity=participant_identity,
        joined_at=datetime.now(timezone.utc),
    )
    db_session.add(row)
    db_session.commit()
    return row


@pytest.mark.live
def test_report_call_quality_records_the_sample(client, db_session):
    token = _signup_and_login(client, "qualitysample1@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]
    _open_participant_session(db_session, _room_session_id(db_session, room_name), me["id"])

    response = client.post(
        f"/media/video/rooms/{room_name}/quality", json={"quality": "good"}, headers=headers
    )
    assert response.status_code == 200, response.text

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["worst_connection_quality"] == "good"
    assert matching["reconnect_count"] == 0

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_report_call_quality_keeps_the_worst_value_seen(client, db_session):
    token = _signup_and_login(client, "qualitysample2@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]
    _open_participant_session(db_session, _room_session_id(db_session, room_name), me["id"])

    client.post(f"/media/video/rooms/{room_name}/quality", json={"quality": "excellent"}, headers=headers)
    client.post(f"/media/video/rooms/{room_name}/quality", json={"quality": "poor"}, headers=headers)
    # A later "excellent" sample must NOT erase the "poor" dip already seen.
    client.post(f"/media/video/rooms/{room_name}/quality", json={"quality": "excellent"}, headers=headers)

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["worst_connection_quality"] == "poor"

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


@pytest.mark.live
def test_report_call_quality_counts_reconnects(client, db_session):
    token = _signup_and_login(client, "qualityreconnect@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/auth/me", headers=headers).json()
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]
    _open_participant_session(db_session, _room_session_id(db_session, room_name), me["id"])

    client.post(
        f"/media/video/rooms/{room_name}/quality",
        json={"quality": "good", "reconnected": True}, headers=headers,
    )
    client.post(
        f"/media/video/rooms/{room_name}/quality",
        json={"quality": "good", "reconnected": True}, headers=headers,
    )

    list_response = client.get("/media/video/rooms", headers=headers)
    matching = next(r for r in list_response.json() if r["room_name"] == room_name)
    assert matching["reconnect_count"] == 2

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


def _room_session_id(db_session, room_name: str) -> str:
    from app.media.models import VideoSession

    return db_session.query(VideoSession).filter(VideoSession.room_name == room_name).first().id


def test_get_call_quality_summary_aggregates_across_participants(db_session):
    from app.media.models import ConnectionQuality, VideoSession, VideoSessionStatus
    from app.media.service import get_call_quality_summary
    from app.numbering.identity.models import Account, AccountType, User, UserRole

    account = Account(name="Quality Summary Co", account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    user = User(account_id=account.id, email="qualitysummary@example.com", role=UserRole.OWNER)
    db_session.add(user)
    db_session.flush()

    session = VideoSession(
        account_id=account.id, host_user_id=user.id, room_name="zl-quality-summary-test",
        status=VideoSessionStatus.ENDED,
    )
    db_session.add(session)
    db_session.flush()

    row_a = _open_participant_session(db_session, session.id, "user-a")
    row_a.worst_connection_quality = ConnectionQuality.GOOD
    row_a.reconnect_count = 1
    row_b = _open_participant_session(db_session, session.id, "user-b")
    row_b.worst_connection_quality = ConnectionQuality.POOR
    row_b.reconnect_count = 2
    db_session.commit()

    summary = get_call_quality_summary(db_session, session.id)
    assert summary == {"worst_connection_quality": "poor", "reconnect_count": 3}
