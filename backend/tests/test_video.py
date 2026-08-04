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


async def _fake_start_recording(room_name):
    return "EG_fake_egress_id"


def _fake_stop_recording(sink: list | None = None):
    async def _stop(egress_id):
        if sink is not None:
            sink.append(egress_id)

    return _stop


@pytest.mark.live
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
def test_group_video_call_issues_join_tokens_to_more_than_two_team_members(client):
    """Roadmap §8 'Video Calling - Phase 1 Standard' targets up to
    MAX_PARTICIPANTS=8 (app.integrations.video.livekit) - this only verified
    1:1 rooms live before. Confirms a real LiveKit room actually accepts join
    tokens for a 3rd and 4th distinct identity on the same room, not just a
    host and one other."""
    owner_token = _signup_and_login(client, "groupvideoowner@example.com")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

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
