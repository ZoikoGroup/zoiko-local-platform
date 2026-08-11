"""Secondary video vendor (Daily.co) behind video_failover_enabled. Real API
calls, not a mock - but NOT tested against a live account, since no real
Daily.co credentials exist yet. Wire DAILY_API_KEY in .env and flip
VIDEO_FAILOVER_ENABLED=true to activate. Callers in livekit.py never change,
since it dispatches to this module by function name only.

Only create_room/end_room are wired to failover today (matching livekit.py's
own scope note) - start_room_recording/stop_room_recording use LiveKit's
Egress API directly and have no secondary path yet.
"""

import httpx

from app.core.config import settings
from app.integrations.video.livekit import VideoError

_ROOMS_URL = "https://api.daily.co/v1/rooms"


def _require_credentials() -> None:
    if not settings.daily_api_key:
        raise VideoError("Secondary video provider (Daily.co) is not configured - set DAILY_API_KEY")


async def create_room(room_name: str, max_participants: int | None = None) -> dict:
    _require_credentials()
    from app.integrations.video.livekit import MAX_PARTICIPANTS

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                _ROOMS_URL,
                headers={"Authorization": f"Bearer {settings.daily_api_key}"},
                json={"name": room_name, "properties": {"max_participants": max_participants or MAX_PARTICIPANTS}},
                timeout=15.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise VideoError(f"Daily.co create_room failed: {e}") from e

    room = response.json()
    return {"name": room["name"], "sid": room["id"]}


async def end_room(room_name: str) -> None:
    _require_credentials()
    async with httpx.AsyncClient() as client:
        try:
            response = await client.delete(
                f"{_ROOMS_URL}/{room_name}", headers={"Authorization": f"Bearer {settings.daily_api_key}"},
                timeout=15.0,
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            raise VideoError(f"Daily.co end_room failed: {e}") from e
