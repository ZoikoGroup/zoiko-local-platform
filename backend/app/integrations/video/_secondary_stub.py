"""Stand-in for a second video vendor behind video_failover_enabled. No real
second-vendor account exists yet - every function raises a clearly labeled
error instead of silently no-opping. Only the two functions livekit.py
currently wraps (create_room/end_room - the pair also wired to the
zoiko.video event topic) have a stub; extending failover to
start_room_recording/stop_room_recording later follows the same pattern.
"""

from app.integrations.video.livekit import VideoError

_NOT_CONFIGURED = (
    "secondary video provider not configured - set VIDEO_SECONDARY_* "
    "credentials once a second vendor account exists"
)


async def create_room(room_name: str) -> dict:
    raise VideoError(_NOT_CONFIGURED)


async def end_room(room_name: str) -> None:
    raise VideoError(_NOT_CONFIGURED)
