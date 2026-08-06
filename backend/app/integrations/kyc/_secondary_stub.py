"""Secondary KYC/identity-verification vendor (Sumsub) behind
kyc_failover_enabled. Real API calls, not a mock - but NOT tested against a
live account, since no real Sumsub credentials exist yet. Wire
SUMSUB_APP_TOKEN/SUMSUB_SECRET_KEY in .env and flip
KYC_FAILOVER_ENABLED=true to activate. Callers in stripe_identity.py never
change, since it dispatches to this module by function name only.

Sumsub has no single "create a hosted verification session" call like
Stripe Identity - it's applicant-create, then a WebSDK access token, then a
hosted link built from that token. Every request is HMAC-signed (Sumsub's
X-App-Access-Sig scheme: HMAC-SHA256 over timestamp+method+path+body), not
a bearer token, so this is the one secondary in this codebase that needs
its own request-signing helper rather than a plain Authorization header.
"""

import hashlib
import hmac
import time

import httpx

from app.core.config import settings
from app.integrations.kyc.stripe_identity import KYCError

_BASE_URL = "https://api.sumsub.com"


def _signed_headers(method: str, path: str, body: bytes = b"") -> dict:
    ts = int(time.time())
    signature = hmac.new(
        settings.sumsub_secret_key.encode("utf-8"), digestmod=hashlib.sha256
    )
    signature.update(str(ts).encode("utf-8") + method.upper().encode("utf-8") + path.encode("utf-8") + body)
    return {
        "X-App-Token": settings.sumsub_app_token,
        "X-App-Access-Sig": signature.hexdigest(),
        "X-App-Access-Ts": str(ts),
    }


def create_verification_session(reference_id: str) -> dict:
    if not (settings.sumsub_app_token and settings.sumsub_secret_key):
        raise KYCError("Secondary KYC provider (Sumsub) is not configured - set SUMSUB_APP_TOKEN/SUMSUB_SECRET_KEY")

    applicant_path = f"/resources/applicants?levelName={settings.sumsub_level_name}"
    applicant_body = {"externalUserId": reference_id}
    import json as _json

    body_bytes = _json.dumps(applicant_body).encode("utf-8")
    try:
        response = httpx.post(
            f"{_BASE_URL}{applicant_path}",
            headers={**_signed_headers("POST", applicant_path, body_bytes), "Content-Type": "application/json"},
            content=body_bytes,
            timeout=15.0,
        )
        response.raise_for_status()
        applicant_id = response.json()["id"]
    except httpx.HTTPError as e:
        raise KYCError(f"Sumsub create applicant failed: {e}") from e

    token_path = f"/resources/accessTokens?userId={reference_id}&levelName={settings.sumsub_level_name}"
    try:
        response = httpx.post(
            f"{_BASE_URL}{token_path}", headers=_signed_headers("POST", token_path), timeout=15.0,
        )
        response.raise_for_status()
        access_token = response.json()["token"]
    except httpx.HTTPError as e:
        raise KYCError(f"Sumsub access token request failed: {e}") from e

    # Sumsub's hosted Web SDK link format - the customer opens this to
    # complete document capture, same role as Stripe Identity's session.url.
    return {"id": applicant_id, "url": f"https://in.sumsub.com/websdk/p/{access_token}"}
