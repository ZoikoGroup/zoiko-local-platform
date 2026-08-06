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

Every function here is entirely local — no HTTP calls, no external
service, nothing to configure. "Sync" means: generate a fake reference
id and return the shape a real response would have. Per the Provider
Gateway rule, this stays the ONLY file real ZoikoNex client code would
ever go in — everything else calls app.billing.service, never this
module directly.
"""

import uuid


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
