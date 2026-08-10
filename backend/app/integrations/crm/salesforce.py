"""
Provider Gateway for Salesforce (integrations/crm category) - REAL
implementation, same posture as app.integrations.crm.hubspot (Pipedrive
still uses app.integrations.crm.mock). Per the Provider Gateway rule, this
stays the ONLY file real Salesforce client code goes in - everything else
calls app.crm.service, never this module.

Built against Salesforce's publicly documented OAuth 2.0 Web Server Flow +
REST API contract (https://developer.salesforce.com), but not yet
exercised against a live org - salesforce_client_id/salesforce_client_secret
are empty until a real Connected App is created (see core/config.py's
docstring for those settings). is_configured() gates every function that
needs real credentials.

Two real differences from HubSpot worth knowing:
1. There is no fixed API host - every org has its own domain
   (instance_url), returned once at OAuth time and required on every
   subsequent call.
2. Access tokens don't come with a told-to-you expiry - Salesforce expects
   callers to just use the token and react to a 401 by refreshing, not to
   pre-emptively refresh on a timer the way HubSpot's expires_in allows.
   exchange_code_for_tokens/refresh_access_token therefore return no
   expiry, and API calls raise SalesforceAuthExpiredError specifically on
   401 so the caller (app.crm.service) can refresh-and-retry once.
"""

from urllib.parse import urlencode

import httpx

from app.core.config import settings
from app.observability.service import trace_provider_call

_API_VERSION = "v59.0"
# api: general REST API access. refresh_token: issue a refresh token at all
# (Salesforce omits it otherwise). offline_access is Salesforce's newer
# name for the same thing, included for forward compatibility.
SCOPES = "api refresh_token offline_access"


class SalesforceError(Exception):
    """Raised instead of letting an httpx/Salesforce-specific error escape this module."""


class SalesforceAuthExpiredError(SalesforceError):
    """Raised specifically on a 401 from the REST API - Salesforce's signal
    that the access token needs a reactive refresh, not a pre-emptive one."""


def is_configured() -> bool:
    return bool(settings.salesforce_client_id and settings.salesforce_client_secret and settings.salesforce_redirect_uri)


def build_authorize_url(*, state: str) -> str:
    if not is_configured():
        raise SalesforceError("Salesforce client ID/secret/redirect URI are not configured")
    params = {
        "response_type": "code",
        "client_id": settings.salesforce_client_id,
        "redirect_uri": settings.salesforce_redirect_uri,
        "scope": SCOPES,
        "state": state,
    }
    return f"{settings.salesforce_login_base_url}/services/oauth2/authorize?{urlencode(params)}"


def exchange_code_for_tokens(code: str) -> dict:
    """Returns {"access_token", "refresh_token", "instance_url", "identity_url"}."""
    if not is_configured():
        raise SalesforceError("Salesforce client ID/secret/redirect URI are not configured")
    try:
        with trace_provider_call("salesforce", "exchange_code_for_tokens"):
            response = httpx.post(
                f"{settings.salesforce_login_base_url}/services/oauth2/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": settings.salesforce_client_id,
                    "client_secret": settings.salesforce_client_secret,
                    "redirect_uri": settings.salesforce_redirect_uri,
                    "code": code,
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise SalesforceError(f"Salesforce token exchange failed: {e}") from e
    body = response.json()
    return {
        "access_token": body["access_token"],
        "refresh_token": body["refresh_token"],
        "instance_url": body["instance_url"],
        "identity_url": body.get("id"),
    }


def refresh_access_token(refresh_token: str) -> dict:
    """Returns {"access_token", "instance_url"} - Salesforce does not
    reissue a refresh token on refresh; keep using the original one."""
    if not is_configured():
        raise SalesforceError("Salesforce client ID/secret/redirect URI are not configured")
    try:
        with trace_provider_call("salesforce", "refresh_access_token"):
            response = httpx.post(
                f"{settings.salesforce_login_base_url}/services/oauth2/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": settings.salesforce_client_id,
                    "client_secret": settings.salesforce_client_secret,
                    "refresh_token": refresh_token,
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise SalesforceError(f"Salesforce token refresh failed: {e}") from e
    body = response.json()
    return {"access_token": body["access_token"], "instance_url": body["instance_url"]}


def get_org_label(access_token: str, identity_url: str | None) -> str:
    """Human-readable "connected as" label, via Salesforce's own identity
    URL returned at OAuth time (its shape - {instance}/id/{orgId}/{userId} -
    is org-specific, so it must be fetched fresh, not constructed)."""
    if not identity_url:
        return "Salesforce"
    try:
        with trace_provider_call("salesforce", "get_org_label"):
            response = httpx.get(identity_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=15.0)
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise SalesforceError(f"Could not fetch Salesforce identity info: {e}") from e
    body = response.json()
    org_name = body.get("organization_id")
    username = body.get("username")
    return f"Salesforce ({username})" if username else (f"Salesforce (org {org_name})" if org_name else "Salesforce")


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise SalesforceAuthExpiredError("Salesforce access token was rejected (401) - needs a refresh")
    response.raise_for_status()


def upsert_contact(access_token: str, instance_url: str, *, phone_number: str, name: str) -> dict:
    """Finds a Salesforce Contact by phone number and updates it, or
    creates a new one. Contact.LastName is a mandatory Salesforce field
    with no single-name equivalent - the full name is used as-is, same
    simplification HubSpot's firstname-only upsert makes in the other
    direction."""
    headers = {"Authorization": f"Bearer {access_token}"}
    escaped_phone = phone_number.replace("'", "\\'")
    soql = f"SELECT Id FROM Contact WHERE Phone = '{escaped_phone}' LIMIT 1"
    try:
        with trace_provider_call("salesforce", "upsert_contact"):
            search = httpx.get(
                f"{instance_url}/services/data/{_API_VERSION}/query",
                headers=headers, params={"q": soql}, timeout=15.0,
            )
            _raise_for_status(search)
            records = search.json().get("records", [])

            if records:
                contact_id = records[0]["Id"]
                update = httpx.patch(
                    f"{instance_url}/services/data/{_API_VERSION}/sobjects/Contact/{contact_id}",
                    headers=headers, json={"Phone": phone_number, "LastName": name}, timeout=15.0,
                )
                _raise_for_status(update)
                return {"external_ref": contact_id}

            create = httpx.post(
                f"{instance_url}/services/data/{_API_VERSION}/sobjects/Contact",
                headers=headers, json={"Phone": phone_number, "LastName": name}, timeout=15.0,
            )
            _raise_for_status(create)
            return {"external_ref": create.json()["id"]}
    except httpx.HTTPError as e:
        raise SalesforceError(f"Salesforce contact upsert failed: {e}") from e


def log_activity(access_token: str, instance_url: str, *, contact_external_ref: str | None, event_type: str, description: str) -> dict:
    """Logs a call/voicemail as a completed Task, associated to the contact
    via WhoId when one was already synced - Salesforce's standard object
    for logged activity (no separate "Note" concept needed here)."""
    headers = {"Authorization": f"Bearer {access_token}"}
    payload: dict = {
        "Subject": event_type.replace("_", " ").title(),
        "Description": description,
        "Status": "Completed",
    }
    if contact_external_ref:
        payload["WhoId"] = contact_external_ref
    try:
        with trace_provider_call("salesforce", "log_activity"):
            response = httpx.post(
                f"{instance_url}/services/data/{_API_VERSION}/sobjects/Task",
                headers=headers, json=payload, timeout=15.0,
            )
            _raise_for_status(response)
    except httpx.HTTPError as e:
        raise SalesforceError(f"Salesforce activity log failed: {e}") from e
    return {"external_ref": response.json()["id"]}
