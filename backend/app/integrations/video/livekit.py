"""
Provider Gateway for LiveKit (video category). Per CLAUDE.md's Provider
Gateway rule, this is the ONLY file allowed to import the `livekit` SDK
directly — everything else calls the functions below instead.
"""

from livekit import api as livekit_api

from app.core.config import settings
from app.integrations._shared.circuit_breaker import CircuitBreaker, with_failover_async
from app.observability.service import trace_provider_call

_breaker = CircuitBreaker("video")


def circuit_state() -> str:
    return _breaker.state.value


class VideoError(Exception):
    """Raised instead of letting a livekit-specific exception escape this module."""


# Imported after VideoError is defined - _secondary_stub imports it back
# from this module, which would otherwise be a circular import.
from app.integrations.video import _secondary_stub as secondary  # noqa: E402

# Phase 3 "larger meetings" - raised from the Phase 1 doctrine's "1:1 and
# small-group, target up to 8 participants" (Roadmap doc §8). 50 is a
# deliberate mid-point: large enough to be a genuine "larger meeting" tier,
# not an attempt at Zoom/Teams-scale webinars (architecture doc's own
# "not a full Zoom, Teams, or call-center clone" boundary) - LiveKit's SFU
# handles this comfortably without special provisioning.
MAX_PARTICIPANTS = 50


def _client() -> livekit_api.LiveKitAPI:
    # Explicit check, not left to the SDK's own validation - LiveKitAPI's
    # constructor does `url = url or os.getenv("LIVEKIT_URL")` internally,
    # silently falling back to the raw OS environment variable whenever a
    # falsy value is passed in. That means an intentionally-unset
    # settings.livekit_url (e.g. this test/config path) would still get a
    # working client built from whatever's in the process environment,
    # never actually raising - confirmed live, this is exactly what let a
    # "LiveKit not configured" scenario silently create a real room instead
    # of failing cleanly. Checking here, before ever calling the SDK,
    # makes this app's own settings object authoritative regardless of
    # what's sitting in os.environ.
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        raise ValueError("LiveKit is not configured (LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET)")
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


async def create_room(room_name: str, max_participants: int = MAX_PARTICIPANTS) -> dict:
    """max_participants defaults to the platform-wide ceiling but should
    normally be the caller's actual billing-plan limit (see
    media.service.create_video_session) - a "larger meetings" plan and a
    Phase 1 starter plan shouldn't get the same room capacity."""
    async def _primary() -> dict:
        # _client() itself raises (a plain ValueError, not TwirpError) when
        # LIVEKIT_URL/KEY/SECRET aren't configured - guarded separately so
        # that case gets the same clean VideoError as a real API failure,
        # instead of an unhandled 500.
        try:
            client = _client()
        except ValueError as e:
            raise VideoError(str(e)) from e

        try:
            with trace_provider_call("livekit", "create_room"):
                room = await client.room.create_room(
                    livekit_api.CreateRoomRequest(name=room_name, max_participants=max_participants)
                )
        except livekit_api.TwirpError as e:
            raise VideoError(str(e)) from e
        finally:
            await client.aclose()
        return {"name": room.name, "sid": room.sid}

    secondary_fn = (
        (lambda: secondary.create_room(room_name, max_participants)) if settings.video_failover_enabled else None
    )
    return await with_failover_async(_breaker, _primary, secondary_fn, VideoError)


async def end_room(room_name: str) -> None:
    async def _primary() -> None:
        try:
            client = _client()
        except ValueError as e:
            raise VideoError(str(e)) from e

        try:
            with trace_provider_call("livekit", "end_room"):
                await client.room.delete_room(livekit_api.DeleteRoomRequest(room=room_name))
        except livekit_api.TwirpError as e:
            # A room LiveKit has already torn down server-side (it idles out
            # on its own after everyone leaves) is not a failure to "end" -
            # ending an already-gone room is exactly the state we wanted.
            # Confirmed live: a stale ACTIVE VideoSession row whose real
            # room no longer exists on LiveKit's side previously made this
            # unrecoverable without direct database access.
            if e.code == "not_found":
                return
            raise VideoError(str(e)) from e
        finally:
            await client.aclose()

    secondary_fn = (lambda: secondary.end_room(room_name)) if settings.video_failover_enabled else None
    await with_failover_async(_breaker, _primary, secondary_fn, VideoError)


async def start_room_recording(room_name: str, object_key: str) -> str:
    """Starts a composite (mixed) recording of the whole room, uploaded to
    the configured S3-compatible bucket - LiveKit's Egress API has no free
    built-in storage, every request must specify a real destination. Returns
    the egress_id, needed to stop it later and to correlate the webhook's
    egress_ended event back to this session.

    object_key is the caller's choice of S3 path (see media.service.
    start_video_recording) - deliberately not derived from room_name here,
    since room_name is an internal random id ("zl-<uuid hex>"), unfit for a
    filename a human ever sees.
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
                            filepath=object_key,
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
        # An egress LiveKit already finished on its own (the room ended, or
        # the recording completed) before our webhook told us so is not a
        # failure to stop it - "already stopped" is exactly the outcome
        # being asked for. Confirmed live: is_recording_in_progress can be
        # true in our DB (recording_url not attached yet) while LiveKit's
        # own egress status is already EGRESS_COMPLETE, previously making
        # end_video_session unrecoverable for that session.
        if e.code == "failed_precondition":
            return
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()


async def get_egress_status(egress_id: str) -> dict | None:
    """Direct poll of LiveKit's own record of an egress job - the source of
    truth the egress_ended webhook is only ever a notification about.
    Confirmed live (2026-08-21): a webhook can go missing (e.g. our own
    backend restarting mid-delivery) for a recording that genuinely
    completed successfully on LiveKit's side, with a real file already
    uploaded - blindly trusting elapsed time and declaring it failed would
    have permanently hidden a real, existing recording from the customer.
    Returns None if LiveKit has no record of this egress_id at all."""
    try:
        client = _client()
    except ValueError as e:
        raise VideoError(str(e)) from e

    try:
        with trace_provider_call("livekit", "get_egress_status"):
            result = await client.egress.list_egress(livekit_api.ListEgressRequest(egress_id=egress_id))
    except livekit_api.TwirpError as e:
        raise VideoError(str(e)) from e
    finally:
        await client.aclose()

    if not result.items:
        return None
    info = result.items[0]
    location = info.file_results[0].location if info.file_results else info.file.location
    return {"status": info.status, "location": location or None, "error": info.error}


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
