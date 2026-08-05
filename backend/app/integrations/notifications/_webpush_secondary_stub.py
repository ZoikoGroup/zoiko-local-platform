"""Stand-in for a second Web Push relay behind webpush_failover_enabled. No
real second-vendor configuration exists yet - raises a clearly labeled error
instead of silently no-opping. Note this category is a slightly awkward fit
for "vendor failover": the push destination (the browser's own push service)
is fixed by the subscription, not chosen by us, so a secondary here would
only matter if swapping which library/relay originates the push.
"""

from app.integrations.notifications.webpush import PushError

_NOT_CONFIGURED = (
    "secondary web push provider not configured - set WEBPUSH_SECONDARY_* "
    "credentials once a second vendor/relay exists"
)


def send_push(*, endpoint: str, p256dh: str, auth: str, title: str, body: str) -> None:
    raise PushError(_NOT_CONFIGURED)
