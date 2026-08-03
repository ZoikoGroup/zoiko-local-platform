import base64
import hashlib

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


def test_create_room_fails_cleanly_when_livekit_url_is_not_configured(client, monkeypatch):
    """A missing LIVEKIT_URL makes the SDK's own client constructor raise a
    plain ValueError (not a TwirpError) - must still surface as a clean 502,
    not an unhandled 500."""
    monkeypatch.setattr("app.integrations.video.livekit.settings.livekit_url", "")
    token = _signup_and_login(client, "videonolivekit@example.com")

    response = client.post("/media/video/rooms", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 502
    assert "url must be set" in response.json()["detail"]


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


def test_start_recording_requires_consent(client):
    token = _signup_and_login(client, "videorecordnoconsent@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    room_name = client.post("/media/video/rooms", headers=headers).json()["room_name"]

    response = client.post(f"/media/video/rooms/{room_name}/recording/start", headers=headers)
    assert response.status_code == 403
    assert "consent" in response.json()["detail"].lower()

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


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


async def _fake_start_recording(room_name):
    return "EG_fake_egress_id"


def _fake_stop_recording(sink: list | None = None):
    async def _stop(egress_id):
        if sink is not None:
            sink.append(egress_id)

    return _stop


def test_member_cannot_start_recording_on_a_room_they_did_not_host(client, monkeypatch):
    """Same host-only restriction as ending a room (Member scoping) -
    recording is the more sensitive of the two actions, so it must not be
    open to any account Member regardless of who hosted the call."""
    monkeypatch.setattr("app.media.service.video.start_room_recording", _fake_start_recording)
    monkeypatch.setattr("app.media.service.video.stop_room_recording", _fake_stop_recording())

    owner_token = _signup_and_login(client, "videorecordhostowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
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
    assert matching["recording_url"] == "https://example.com/recordings/fake.mp4"
    assert matching["recording_in_progress"] is False

    client.post(f"/media/video/rooms/{room_name}/end", headers=headers)


def test_webhook_rejects_invalid_signature(client):
    body, _ = _livekit_webhook_body_and_token("room_finished", "some-room")
    response = client.post(
        "/media/video/webhook", content=body, headers={"Authorization": "not-a-real-token"}
    )
    assert response.status_code == 403


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
