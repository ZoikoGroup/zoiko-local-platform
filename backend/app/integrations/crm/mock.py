"""
Provider Gateway for CRM sync (integrations/crm category). Mock only - no
real HubSpot, Salesforce, or Pipedrive client exists yet, matching the
same posture as app.integrations.billing.zoikonex: explicitly disclosed,
founder-approved as an exception to the Architecture doc's Phase 2 exit
criteria ("start with export and webhook-ready events internally; add
HubSpot, Salesforce, and Pipedrive after call/event data stabilizes").

Every function here is entirely local - no HTTP calls, no OAuth, nothing
to configure. "Connect" and "sync" both mean: generate a fake reference
id and return the shape a real response would have. Per the Provider
Gateway rule, this stays the ONLY file a real CRM client would ever go
in - everything else calls app.crm.service, never this module directly.
"""

import uuid


def connect(*, account_id: str, provider: str) -> dict:
    """Mocks the OAuth handoff a real CRM connection would go through -
    HubSpot/Salesforce/Pipedrive would redirect the customer through a
    real consent screen and hand back an access token; this just fabricates
    a plausible "connected as" label and reference id."""
    return {
        "external_ref": f"crm_{provider}_{uuid.uuid4().hex[:16]}",
        "external_account_label": f"Mock {provider.title()} Workspace",
    }


def sync_contact(*, contact_id: str, account_id: str, name: str, phone_number: str) -> dict:
    return {"external_ref": f"crm_contact_{uuid.uuid4().hex[:16]}"}


def sync_activity(*, account_id: str, event_type: str, contact_phone: str | None) -> dict:
    """Mocks logging a call/voicemail as an "activity" against a CRM
    contact - the piece a real integration would use to make Zoiko Local
    call history show up inside the CRM's own contact timeline."""
    return {"external_ref": f"crm_activity_{uuid.uuid4().hex[:16]}"}
