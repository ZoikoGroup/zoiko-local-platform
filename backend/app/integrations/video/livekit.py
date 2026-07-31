"""
Provider Gateway for LiveKit (video category). Per CLAUDE.md's Provider
Gateway rule, this is the ONLY file allowed to import the `livekit` SDK
directly — everything else calls the functions below instead.
"""

from livekit import api as livekit_api

from app.core.config import settings


class VideoError(Exception):
    """Raised instead of letting a livekit-specific exception escape this module."""


# Roadmap doc §8 "Video Calling - Phase 1 Standard": target up to 8 participants.
MAX_PARTICIPANTS = 8


def _client() -> livekit_api.LiveKitAPI:
    return livekit_api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    )


async def create_room(room_name: str) -> dict:
    client = _client()
    try:
        room = await client.room.create_room(
            livekit_api.CreateRoomRequest(name=room_name, max_participants=MAX_PARTICIPANTS)
        )
    except livekit_api.TwirpError as e:
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()
    return {"name": room.name, "sid": room.sid}


async def end_room(room_name: str) -> None:
    client = _client()
    try:
        await client.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
    except livekit_api.TwirpError as e:
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()


def verify_webhook_event(body: str, auth_token: str) -> livekit_api.WebhookEvent:
    """Verifies and parses a LiveKit webhook (room_started/room_finished/
    participant_joined/left). Requires the webhook URL to be configured in
    the LiveKit Cloud project dashboard — this only verifies traffic that
    already arrives, it doesn't register the URL itself."""
    verifier = livekit_api.TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
    receiver = livekit_api.WebhookReceiver(verifier)
    try:
        return receiver.receive(body, auth_token)
    except Exception as e:
        raise VideoError(f"Invalid LiveKit webhook: {e}") from e


def build_participant_token(room_name: str, identity: str, display_name: str) -> str:
    """Pure JWT signing, no network call — safe to call from any context."""
    grants = livekit_api.VideoGrants(room_join=True, room=room_name)
    token = (
        livekit_api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(display_name)
        .with_grants(grants)
    )
    return token.to_jwt()
