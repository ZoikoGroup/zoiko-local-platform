"""
Provider Gateway for HubSpot (integrations/crm category) - REAL implementation,
unlike app.integrations.crm.mock (which Salesforce and Pipedrive still use).
Per the Provider Gateway rule, this stays the ONLY file real HubSpot client
code goes in - everything else calls app.crm.service, never this module.

Built against HubSpot's publicly documented OAuth + CRM v3 API contract
(https://developers.hubspot.com), but not yet exercised against a live
HubSpot account - hubspot_client_id/hubspot_client_secret are empty until a
real developer app is created (see core/config.py's docstring for those
settings). is_configured() gates every function that needs real credentials,
so the rest of the app can check it before routing to this adapter instead
of the mock.
"""

import time
from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.observability.service import trace_provider_call

_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
_API_BASE = "https://api.hubapi.com"
# Minimum scopes to search/create/update contacts and log call/voicemail
# activity as notes against them.
SCOPES = "crm.objects.contacts.read crm.objects.contacts.write"


class HubSpotError(Exception):
    """Raised instead of letting an httpx/HubSpot-specific error escape this module."""


def is_configured() -> bool:
    return bool(settings.hubspot_client_id and settings.hubspot_client_secret and settings.hubspot_redirect_uri)


def build_authorize_url(*, state: str) -> str:
    if not is_configured():
        raise HubSpotError("HubSpot client ID/secret/redirect URI are not configured")
    params = {
        "client_id": settings.hubspot_client_id,
        "redirect_uri": settings.hubspot_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Returns {"access_token", "refresh_token", "expires_in"} per HubSpot's
    OAuth token endpoint contract."""
    if not is_configured():
        raise HubSpotError("HubSpot client ID/secret/redirect URI are not configured")
    try:
        with trace_provider_call("hubspot", "exchange_code_for_tokens"):
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.hubspot_client_id,
                    "client_secret": settings.hubspot_client_secret,
                    "redirect_uri": settings.hubspot_redirect_uri,
                    "code": code,
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise HubSpotError(f"HubSpot token exchange failed: {e}") from e
    return response.json()


def refresh_access_token(refresh_token: str) -> dict:
    """Same response shape as exchange_code_for_tokens - HubSpot issues a
    new access token (and typically a new refresh token) on every refresh."""
    if not is_configured():
        raise HubSpotError("HubSpot client ID/secret/redirect URI are not configured")
    try:
        with trace_provider_call("hubspot", "refresh_access_token"):
            response = httpx.post(
                _TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.hubspot_client_id,
                    "client_secret": settings.hubspot_client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise HubSpotError(f"HubSpot token refresh failed: {e}") from e
    return response.json()


def get_hub_info(access_token: str) -> dict:
    """Which HubSpot account/hub a token belongs to, via HubSpot's
    access-token introspection endpoint - the closest real analog to the
    mock's fabricated external_ref + "Mock Hubspot Workspace" label.
    Returns {"hub_id", "hub_domain", "label"}."""
    try:
        with trace_provider_call("hubspot", "get_hub_info"):
            response = httpx.get(f"{_API_BASE}/oauth/v1/access-tokens/{access_token}", timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise HubSpotError(f"Could not fetch HubSpot account info: {e}") from e
    body = response.json()
    hub_id = body.get("hub_id")
    hub_domain = body.get("hub_domain")
    return {
        "hub_id": hub_id,
        "hub_domain": hub_domain,
        "label": f"HubSpot ({hub_domain})" if hub_domain else "HubSpot",
    }


def upsert_contact(access_token: str, *, phone_number: str, name: str) -> dict:
    """Finds a HubSpot contact by phone number and updates it, or creates a
    new one - HubSpot's CRM v3 Contacts API has no single upsert-by-phone
    endpoint, so this is search-then-create/update, same shape any real
    client of this API has to do."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with trace_provider_call("hubspot", "upsert_contact"):
            search = httpx.post(
                f"{_API_BASE}/crm/v3/objects/contacts/search",
                headers=headers,
                json={
                    "filterGroups": [
                        {"filters": [{"propertyName": "phone", "operator": "EQ", "value": phone_number}]}
                    ],
                    "limit": 1,
                },
                timeout=15.0,
            )
            search.raise_for_status()
            results = search.json().get("results", [])

            if results:
                contact_id = results[0]["id"]
                update = httpx.patch(
                    f"{_API_BASE}/crm/v3/objects/contacts/{contact_id}",
                    headers=headers,
                    json={"properties": {"phone": phone_number, "firstname": name}},
                    timeout=15.0,
                )
                update.raise_for_status()
                return {"external_ref": contact_id}

            create = httpx.post(
                f"{_API_BASE}/crm/v3/objects/contacts",
                headers=headers,
                json={"properties": {"phone": phone_number, "firstname": name}},
                timeout=15.0,
            )
            create.raise_for_status()
            return {"external_ref": create.json()["id"]}
    except httpx.HTTPError as e:
        raise HubSpotError(f"HubSpot contact upsert failed: {e}") from e


def log_activity(access_token: str, *, contact_external_ref: str | None, event_type: str, note_body: str) -> dict:
    """Logs a call/voicemail as a Note engagement, associated to the
    contact when one was already synced (contact_external_ref) - HubSpot's
    CRM v3 Notes API, association type 202 is the documented
    "note to contact" association category."""
    headers = {"Authorization": f"Bearer {access_token}"}
    payload = {"properties": {"hs_note_body": note_body, "hs_timestamp": _now_ms()}}
    if contact_external_ref:
        payload["associations"] = [
            {
                "to": {"id": contact_external_ref},
                "types": [
                    {"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 202}
                ],
            }
        ]
    try:
        with trace_provider_call("hubspot", "log_activity"):
            response = httpx.post(f"{_API_BASE}/crm/v3/objects/notes", headers=headers, json=payload, timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise HubSpotError(f"HubSpot activity log failed: {e}") from e
    return {"external_ref": response.json()["id"]}


def _now_ms() -> int:
    return int(time.time() * 1000)
