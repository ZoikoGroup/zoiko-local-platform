import base64
import hashlib

from google.protobuf.json_format import MessageToJson
from livekit import api as livekit_api
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
