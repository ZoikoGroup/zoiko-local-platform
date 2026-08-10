"""
Provider Gateway for ZoikoNex (billing category) — MOCK implementation.

Explicitly a stand-in, not a real integration: there is no live ZoikoNex
API to call yet (the event contract was never locked — see
docs/Zoiko_Local_Phase_1_Engineering_Build_Roadmap.docx §15's "Lock
ZoikoNex billing event contract and entitlement model" action item,
still open). Built anyway per an explicit, informed decision to accept
the risk that this gets thrown away once the real contract exists, in
exchange for having the internal seams (subscription sync, usage sync,
payment-state webhook, reconciliation — Architecture doc §9) ready to
wire a real client into immediately when it does.

The sync_* functions are entirely local — no HTTP calls, no external
service, nothing to configure. "Sync" means: generate a fake reference
id and return the shape a real response would have. verify_webhook_signature
is the one real piece here: it genuinely checks an HMAC signature against a
configured secret, ready for ZoikoNex to call us the moment we're given a
real secret and told their actual signing scheme. Per the Provider
Gateway rule, this stays the ONLY file real ZoikoNex client code would
ever go in — everything else calls app.billing.service, never this
module directly.
"""

import hashlib
import hmac
import uuid

from app.core.config import settings


class ZoikoNexWebhookError(Exception):
    """Raised when an inbound ZoikoNex webhook fails signature verification."""


def verify_webhook_signature(payload: bytes, signature_header: str | None) -> None:
    """Verifies an inbound ZoikoNex payment-event webhook. Real ZoikoNex has
    no SDK to hand this off to (unlike Stripe's construct_webhook_event), so
    this hand-rolls HMAC-SHA256 the same way — and in the same header
    convention (`sha256=<hex>`) — as our own outbound webhooks sign theirs
    (see app.webhooks.service._sign). This is a placeholder scheme: the real
    signing method must be confirmed against whatever ZoikoNex's locked
    webhook contract specifies once one exists."""
    if not settings.zoikonex_webhook_secret:
        raise ZoikoNexWebhookError("ZoikoNex webhook secret is not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise ZoikoNexWebhookError("Missing or malformed ZoikoNex webhook signature")

    expected = hmac.new(settings.zoikonex_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise ZoikoNexWebhookError("Invalid ZoikoNex webhook signature")


def sync_subscription(*, subscription_id: str, account_id: str, plan_code: str, status: str) -> dict:
    """Mocks ZoikoNex Subscription sync (Architecture doc §9: "Zoiko Local
    creates or updates plan, seat, number, and add-on entitlement events;
    ZoikoNex converts them into billing schedules")."""
    return {"zoikonex_ref": f"zn_sub_{uuid.uuid4().hex[:16]}", "synced": True}


def sync_usage_event(*, usage_event_id: str, account_id: str, event_type: str, quantity: float, unit: str) -> dict:
    """Mocks ZoikoNex Usage sync (Architecture doc §9: "rated usage inputs
    with idempotency keys: call minutes, country bands, video
    participant-minutes, AI processing units, storage, and premium
    number fees")."""
    return {"zoikonex_ref": f"zn_usage_{uuid.uuid4().hex[:16]}", "synced": True}
