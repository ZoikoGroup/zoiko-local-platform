"""
Provider Gateway for LiveKit (video category). Per CLAUDE.md's Provider
Gateway rule, this is the ONLY file allowed to import the `livekit` SDK
directly — everything else calls the functions below instead.
"""

from livekit import api as livekit_api

from app.core.config import settings
from app.observability.service import trace_provider_call


class VideoError(Exception):
    """Raised instead of letting a livekit-specific exception escape this module."""


# Roadmap doc §8 "Video Calling - Phase 1 Standard": target up to 8 participants.
MAX_PARTICIPANTS = 8


def _client() -> livekit_api.LiveKitAPI:
    return livekit_api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    )


async def health_check() -> dict:
    """Real reachability check - lists rooms (empty result is fine, this
    is just confirming the credentials and endpoint are live)."""
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        return {"configured": False, "ok": False, "detail": None}
    try:
        client = _client()
        try:
            await client.room.list_rooms(livekit_api.ListRoomsRequest())
            return {"configured": True, "ok": True, "detail": None}
        finally:
            await client.aclose()
    except livekit_api.TwirpError as e:
        return {"configured": True, "ok": False, "detail": str(e)}


async def create_room(room_name: str) -> dict:
    # _client() itself raises (a plain ValueError, not TwirpError) when
    # LIVEKIT_URL/KEY/SECRET aren't configured - guarded separately so that
    # case gets the same clean VideoError as a real API failure, instead of
    # an unhandled 500.
    try:
        client = _client()
    except ValueError as e:
        raise VideoError(str(e)) from e

    try:
        with trace_provider_call("livekit", "create_room"):
            room = await client.room.create_room(
                livekit_api.CreateRoomRequest(name=room_name, max_participants=MAX_PARTICIPANTS)
            )
    except livekit_api.TwirpError as e:
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()
    return {"name": room.name, "sid": room.sid}


async def end_room(room_name: str) -> None:
    try:
        client = _client()
    except ValueError as e:
        raise VideoError(str(e)) from e

    try:
        with trace_provider_call("livekit", "end_room"):
            await client.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
    except livekit_api.TwirpError as e:
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()


async def start_room_recording(room_name: str) -> str:
    """Starts a composite (mixed) recording of the whole room, uploaded to
    the configured S3-compatible bucket - LiveKit's Egress API has no free
    built-in storage, every request must specify a real destination. Returns
    the egress_id, needed to stop it later and to correlate the webhook's
    egress_ended event back to this session.
    """
    if not (settings.s3_bucket and settings.s3_access_key_id and settings.s3_secret_access_key):
        raise VideoError(
            "Recording storage is not configured — set S3_BUCKET, S3_ACCESS_KEY_ID and "
            "S3_SECRET_ACCESS_KEY (any S3-compatible provider, e.g. Cloudflare R2)"
        )

    try:
        client = _client()
    except ValueError as e:
        raise VideoError(str(e)) from e

    s3_upload = livekit_api.S3Upload(
        access_key=settings.s3_access_key_id,
        secret=settings.s3_secret_access_key,
        bucket=settings.s3_bucket,
        region=settings.s3_region,
        endpoint=settings.s3_endpoint or None,
        force_path_style=bool(settings.s3_endpoint),
    )

    try:
        with trace_provider_call("livekit", "start_room_recording"):
            egress = await client.egress.start_room_composite_egress(
                livekit_api.RoomCompositeEgressRequest(
                    room_name=room_name,
                    file_outputs=[
                        livekit_api.EncodedFileOutput(
                            file_type=livekit_api.EncodedFileType.MP4,
                            # room_name is already globally unique (zl-<uuid hex>)
                            # and a room is only ever recorded once in this design,
                            # so no need for LiveKit's {time}-style filepath templates.
                            filepath=f"recordings/{room_name}.mp4",
                            s3=s3_upload,
                        )
                    ],
                )
            )
    except livekit_api.TwirpError as e:
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()
    return egress.egress_id


async def stop_room_recording(egress_id: str) -> None:
    try:
        client = _client()
    except ValueError as e:
        raise VideoError(str(e)) from e

    try:
        with trace_provider_call("livekit", "stop_room_recording"):
            await client.egress.stop_egress(livekit_api.StopEgressRequest(egress_id=egress_id))
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
