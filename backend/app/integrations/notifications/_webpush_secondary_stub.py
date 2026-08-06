"""Secondary web push relay (OneSignal) behind webpush_failover_enabled.
Real API calls, not a mock - but NOT tested against a live account, since no
real OneSignal credentials exist yet. Wire ONESIGNAL_APP_ID/ONESIGNAL_API_KEY
in .env and flip WEBPUSH_FAILOVER_ENABLED=true to activate. Callers in
webpush.py never change, since it dispatches to this module by function
name only.

This category is a genuinely awkward fit for "vendor failover" (the push
destination - the browser's own push service - is fixed by the subscription,
not chosen by us), but OneSignal can still originate the push through its
own relay to that same destination using its own subscriber model rather
than pywebpush's raw VAPID delivery - a real alternate delivery path, even
if the fit is looser than telecom/video/LLM's vendor-swap model.
"""

import httpx

from app.core.config import settings
from app.integrations.notifications.webpush import PushError

_NOTIFICATIONS_URL = "https://onesignal.com/api/v1/notifications"


def send_push(*, endpoint: str, p256dh: str, auth: str, title: str, body: str) -> None:
    if not (settings.onesignal_app_id and settings.onesignal_api_key):
        raise PushError(
            "Secondary web push provider (OneSignal) is not configured - set ONESIGNAL_APP_ID/ONESIGNAL_API_KEY"
        )

    try:
        response = httpx.post(
            _NOTIFICATIONS_URL,
            headers={"Authorization": f"Basic {settings.onesignal_api_key}", "Content-Type": "application/json"},
            json={
                "app_id": settings.onesignal_app_id,
                # OneSignal's own subscriber model doesn't map onto a raw
                # Web Push endpoint/p256dh/auth triple - targeting by the
                # existing browser subscription's endpoint URL as an
                # external ID is the closest correspondence available.
                "include_external_user_ids": [endpoint],
                "headings": {"en": title},
                "contents": {"en": body},
            },
            timeout=15.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as e:
        raise PushError(f"OneSignal send failed: {e}") from e
