"""
Provider Gateway for Pipedrive (integrations/crm category) - REAL
implementation, completing the same real-OAuth posture as
app.integrations.crm.hubspot and app.integrations.crm.salesforce (all
three CrmProvider values are now real; app.integrations.crm.mock is kept
only as historical reference, no longer reachable via any live code path).
Per the Provider Gateway rule, this stays the ONLY file real Pipedrive
client code goes in - everything else calls app.crm.service, never this
module.

Built against Pipedrive's publicly documented OAuth 2.0 + REST API contract
(https://pipedrive.readme.io), but not yet exercised against a live
account - pipedrive_client_id/pipedrive_client_secret are empty until a
real Developer Hub app is created (see core/config.py's docstring for
those settings). is_configured() gates every function that needs real
credentials.

Token model is close to HubSpot's: access tokens DO carry a told-to-you
expires_in, so refresh is pre-emptive (see app.crm.service._get_valid_
pipedrive_access_token), not reactive like Salesforce's. Like Salesforce,
though, there's no single fixed API host - every company account gets its
own api_domain, returned at OAuth time and required on every call. Reuses
CrmConnection.instance_url for this (same column Salesforce's instance_url
already uses - Pipedrive's "api_domain" is the same concept under a
different vendor name).
"""

from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.observability.service import trace_provider_call

_AUTHORIZE_URL = "https://oauth.pipedrive.com/oauth/authorize"
_TOKEN_URL = "https://oauth.pipedrive.com/oauth/token"


class PipedriveError(Exception):
    """Raised instead of letting an httpx/Pipedrive-specific error escape this module."""


def is_configured() -> bool:
    return bool(settings.pipedrive_client_id and settings.pipedrive_client_secret and settings.pipedrive_redirect_uri)


def build_authorize_url(*, state: str) -> str:
    if not is_configured():
        raise PipedriveError("Pipedrive client ID/secret/redirect URI are not configured")
    params = {
        "client_id": settings.pipedrive_client_id,
        "redirect_uri": settings.pipedrive_redirect_uri,
        "state": state,
    }
    return f"{_AUTHORIZE_URL}?{urlencode(params)}"


def _client_auth() -> tuple[str, str]:
    return (settings.pipedrive_client_id, settings.pipedrive_client_secret)


def exchange_code_for_tokens(code: str) -> dict:
    """Returns {"access_token", "refresh_token", "api_domain", "expires_in"}."""
    if not is_configured():
        raise PipedriveError("Pipedrive client ID/secret/redirect URI are not configured")
    try:
        with trace_provider_call("pipedrive", "exchange_code_for_tokens"):
            response = httpx.post(
                _TOKEN_URL,
                auth=_client_auth(),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.pipedrive_redirect_uri,
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise PipedriveError(f"Pipedrive token exchange failed: {e}") from e
    body = response.json()
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "api_domain": body["api_domain"],
        "expires_in": body["expires_in"],
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Same response shape as exchange_code_for_tokens."""
    if not is_configured():
        raise PipedriveError("Pipedrive client ID/secret/redirect URI are not configured")
    try:
        with trace_provider_call("pipedrive", "refresh_access_token"):
            response = httpx.post(
                _TOKEN_URL,
                auth=_client_auth(),
                data={"grant_type": "refresh_token", "refresh_token": refresh_token},
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise PipedriveError(f"Pipedrive token refresh failed: {e}") from e
    body = response.json()
    return {
        "access_token": body["access_token"],
        "refresh_token": body.get("refresh_token", refresh_token),
        "api_domain": body["api_domain"],
        "expires_in": body["expires_in"],
    }


def get_account_label(access_token: str, api_domain: str) -> str:
    """Human-readable "connected as" label, via Pipedrive's own current-user endpoint."""
    try:
        with trace_provider_call("pipedrive", "get_account_label"):
            response = httpx.get(
                f"{api_domain}/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"}, timeout=15.0
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise PipedriveError(f"Could not fetch Pipedrive account info: {e}") from e
    data = response.json().get("data") or {}
    company_name = data.get("company_name")
    return f"Pipedrive ({company_name})" if company_name else "Pipedrive"


def upsert_contact(access_token: str, api_domain: str, *, phone_number: str, name: str) -> dict:
    """Finds a Pipedrive Person by phone number and updates it, or creates
    a new one - Pipedrive's REST API has no single upsert-by-phone
    endpoint, so this is search-then-create/update, same shape HubSpot's
    and Salesforce's adapters use."""
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        with trace_provider_call("pipedrive", "upsert_contact"):
            search = httpx.get(
                f"{api_domain}/api/v1/persons/search",
                headers=headers, params={"term": phone_number, "fields": "phone", "exact_match": "true"},
                timeout=15.0,
            )
            search.raise_for_status()
            items = (search.json().get("data") or {}).get("items") or []

            if items:
                person_id = items[0]["item"]["id"]
                update = httpx.put(
                    f"{api_domain}/api/v1/persons/{person_id}",
                    headers=headers, json={"name": name, "phone": phone_number}, timeout=15.0,
                )
                update.raise_for_status()
                return {"external_ref": str(person_id)}

            create = httpx.post(
                f"{api_domain}/api/v1/persons",
                headers=headers, json={"name": name, "phone": phone_number}, timeout=15.0,
            )
            create.raise_for_status()
            return {"external_ref": str(create.json()["data"]["id"])}
    except httpx.HTTPError as e:
        raise PipedriveError(f"Pipedrive contact upsert failed: {e}") from e


def log_activity(access_token: str, api_domain: str, *, contact_external_ref: str | None, event_type: str, subject: str) -> dict:
    """Logs a call/voicemail as a completed Activity, linked to the Person
    via person_id when one was already synced - Pipedrive's standard
    object for logged activity."""
    headers = {"Authorization": f"Bearer {access_token}"}
    payload: dict = {"subject": subject, "type": "call", "done": 1}
    if contact_external_ref:
        payload["person_id"] = int(contact_external_ref)
    try:
        with trace_provider_call("pipedrive", "log_activity"):
            response = httpx.post(f"{api_domain}/api/v1/activities", headers=headers, json=payload, timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise PipedriveError(f"Pipedrive activity log failed: {e}") from e
    return {"external_ref": str(response.json()["data"]["id"])}
