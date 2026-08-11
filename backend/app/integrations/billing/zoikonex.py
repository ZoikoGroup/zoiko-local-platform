"""
Provider Gateway for ZoikoNex (billing category) — real client, built and
tested against a locally self-hosted copy of the ZoikoNex backend
(github.com/Zoiko-Nex/backend), reading each service's own
API.INTEGRATION.md and Go source directly rather than guessing.

Auth: OAuth2 client_credentials against ZoikoNex's own identity-tenancy
service (RS256 JWT, ~15min expiry) - NOT a bare API key. Register a
ServiceAccount + client_credentials row there (see identity-tenancy's
seed/seed.sql for the shape) and put its id/secret in
settings.zoikonex_client_id/_secret.

What's real here:
- Party -> Customer -> Account creation (customer-account service) -
  tested end to end against a live local instance.
- Raw usage ingestion (usage-ingestion-mediation's /v1/usage/ingest) -
  tested end to end.
- Plan catalog registration (product-catalogue-commercial: Product +
  Offer + PriceRule) - tested end to end, but NOT wired into any
  automatic flow (see register_plan_in_catalog's docstring on why).

What's NOT done here, deliberately:
- Real per-unit usage RATING against ZoikoNex (usage-ingestion-mediation's
  own /rate endpoint and rating-charging's /v1/postpaid/rate both require
  the CALLER to supply rate_per_unit - neither looks it up from the
  catalog automatically - and Zoiko Local's Plan model has no price
  fields yet (no price has been commercially decided). Cost estimation
  still uses Zoiko Local's own CallingRate table, same as the mock this
  replaces - inventing a rate_per_unit to satisfy the API would mean
  guessing a real price, the one thing this whole architecture exists to
  prevent.
- Invoicing (billing-invoice) and payment collection (payments) - not
  built this pass; the customer/usage plumbing above needs to be proven
  in real use first, and ZoikoNex's own architecture registry marks the
  usage->rated-charge flow as not yet proven end-to-end even on their
  side (see their architecture/registry.md ZN-RISK-002).

verify_webhook_signature's scheme (HMAC-SHA256, `X-ZoikoNex-Signature:
sha256=<hex>`) is confirmed correct against ZoikoNex payments' own
API.INTEGRATION.md - unchanged from the mock.
"""

import hashlib
import hmac
import math
import time
import uuid

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.observability.service import trace_provider_call
from app.usage.models import DEFAULT_RATE_COUNTRY, CallingRate


class ZoikoNexError(Exception):
    """Raised for any ZoikoNex API failure - carries the vendor's own
    error `code`/`retryable` when the response matched their error
    envelope, so callers can distinguish a transient fault from a real
    rejection without re-parsing the response themselves."""

    def __init__(self, message: str, *, code: str | None = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ZoikoNexWebhookError(Exception):
    """Raised when an inbound ZoikoNex payment-event webhook fails signature verification."""


def verify_webhook_signature(payload: bytes, signature_header: str | None) -> None:
    """Verifies an inbound ZoikoNex webhook - HMAC-SHA256 over the raw
    body, `sha256=<hex>` header convention. Confirmed against
    ZoikoNex payments' own API.INTEGRATION.md (`X-ZoikoNex-Signature`)."""
    if not settings.zoikonex_webhook_secret:
        raise ZoikoNexWebhookError("ZoikoNex webhook secret is not configured")
    if not signature_header or not signature_header.startswith("sha256="):
        raise ZoikoNexWebhookError("Missing or malformed ZoikoNex webhook signature")

    expected = hmac.new(settings.zoikonex_webhook_secret.encode(), payload, hashlib.sha256).hexdigest()
    provided = signature_header.removeprefix("sha256=")
    if not hmac.compare_digest(expected, provided):
        raise ZoikoNexWebhookError("Invalid ZoikoNex webhook signature")


# --- Auth: OAuth2 client_credentials against identity-tenancy ---

# Process-wide cache - a fresh token per call would mint an unnecessary new
# 15-minute JWT (and a new audit-visible session) on every single ZoikoNex
# request. Refreshed 30s before actual expiry as a clock-skew buffer.
_token_cache: dict[str, str | float | None] = {"access_token": None, "expires_at": 0.0}


def _get_access_token() -> str:
    if not settings.zoikonex_client_id or not settings.zoikonex_client_secret:
        raise ZoikoNexError("ZoikoNex client credentials are not configured")

    now = time.time()
    if _token_cache["access_token"] and _token_cache["expires_at"] > now + 30:
        return _token_cache["access_token"]

    try:
        with trace_provider_call("zoikonex", "oauth_token"):
            response = httpx.post(
                f"{settings.zoikonex_identity_url}/oauth/v1/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": settings.zoikonex_client_id,
                    "client_secret": settings.zoikonex_client_secret,
                },
                timeout=15.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        raise ZoikoNexError(f"ZoikoNex token request failed: {e}") from e

    body = response.json()
    _token_cache["access_token"] = body["access_token"]
    _token_cache["expires_at"] = now + body.get("expires_in", 900)
    return _token_cache["access_token"]


def _request(method: str, base_url: str, path: str, **kwargs) -> dict:
    """Shared HTTP helper - attaches the Bearer token and normalizes every
    ZoikoNex service's shared error envelope (`code`/`message`/`retryable`)
    into ZoikoNexError, so callers never touch httpx directly."""
    token = _get_access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"

    try:
        with trace_provider_call("zoikonex", f"{method} {path}"):
            response = httpx.request(method, f"{base_url}{path}", headers=headers, timeout=20.0, **kwargs)
    except httpx.HTTPError as e:
        raise ZoikoNexError(f"ZoikoNex request to {path} failed: {e}") from e

    if response.status_code >= 400:
        try:
            error_body = response.json()
        except ValueError:
            error_body = {}
        raise ZoikoNexError(
            error_body.get("message", f"ZoikoNex returned HTTP {response.status_code} for {path}"),
            code=error_body.get("code"),
            retryable=bool(error_body.get("retryable")),
        )
    return response.json() if response.content else {}


# --- Plan catalog registration (product-catalogue-commercial) ---

def register_plan_in_catalog(
    db: Session, plan, *, amount_minor_units: int, currency_code: str = "USD"
) -> dict:
    """One-time registration of a Zoiko Local Plan as a ZoikoNex
    Product + Offer + PriceRule - idempotent (a plan already carrying
    zoikonex_product_id is returned as-is, never re-registered).

    Deliberately NOT called automatically from anywhere (e.g. subscription
    sync) - amount_minor_units is a real commercial price ZoikoNex's
    catalog will treat as authoritative, and Plan itself has no price
    fields because that decision hasn't been made (see Plan's docstring).
    Call this explicitly, once, per plan, only after a real price is
    decided - matches the same "staff sets real data, code never invents
    it" discipline as app.usage.service.upsert_calling_rate.
    """
    if plan.zoikonex_product_id:
        return {
            "product_id": plan.zoikonex_product_id,
            "offer_id": plan.zoikonex_offer_id,
            "price_rule_id": plan.zoikonex_price_rule_id,
        }

    product = _request(
        "POST", settings.zoikonex_catalog_url, "/v1/products",
        json={
            "product_name": f"Zoiko Local — {plan.name}",
            "product_type": "POSTPAID",
            "charging_model": "OFFLINE",
            "charge_structure": "RECURRING",
            "currency_code": currency_code,
        },
    )
    offer = _request(
        "POST", settings.zoikonex_catalog_url, "/v1/offers",
        json={"product_id": product["id"], "offer_name": f"{plan.name} — Standard"},
    )
    price_rule = _request(
        "POST", settings.zoikonex_catalog_url, "/v1/price-rules",
        json={
            "product_id": product["id"],
            "offer_id": offer["id"],
            "amount_minor_units": amount_minor_units,
            "currency_code": currency_code,
            "charge_structure": "RECURRING",
            "billing_period": "MONTHLY",
        },
    )

    plan.zoikonex_product_id = product["id"]
    plan.zoikonex_offer_id = offer["id"]
    plan.zoikonex_price_rule_id = price_rule["id"]
    db.commit()

    return {"product_id": product["id"], "offer_id": offer["id"], "price_rule_id": price_rule["id"]}


# --- Customer identity sync (customer-account) ---

_PARTY_TYPE_FOR_ACCOUNT_TYPE = {"business": "BUSINESS", "individual": "INDIVIDUAL"}
_CUSTOMER_TYPE_FOR_ACCOUNT_TYPE = {"business": "ENTERPRISE", "individual": "RETAIL"}


def sync_subscription(db: Session, sub, *, account_type: str) -> dict:
    """Ensures this account's ZoikoNex Party -> Customer -> Account chain
    exists - idempotent (an already-linked Subscription is returned as-is,
    never re-created). ZoikoNex has no separate "Subscription" resource of
    its own: what usage ingestion and (eventually) billing actually
    reference is this customer_id/account_id plus a product_id at rating
    time, not a "subscribe to a plan" call - see this module's docstring.

    pii_token stands in for a real vaulted PII token (ZN-ADR-013) -
    identity-tenancy's PII vault has no reachable HTTP or gRPC route in
    the ZoikoNex backend as of this build (the domain logic exists,
    nothing calls it), and customer-account's own create-party handler
    only checks the token is non-empty, never resolves it. Swap this for
    a real vault call the moment ZoikoNex exposes one.
    """
    if sub.zoikonex_account_id:
        return {
            "party_id": sub.zoikonex_party_id,
            "customer_id": sub.zoikonex_customer_id,
            "account_id": sub.zoikonex_account_id,
        }

    pii_token = sub.zoikonex_pii_token or str(uuid.uuid4())
    party_type = _PARTY_TYPE_FOR_ACCOUNT_TYPE.get(account_type, "INDIVIDUAL")
    customer_type = _CUSTOMER_TYPE_FOR_ACCOUNT_TYPE.get(account_type, "RETAIL")

    party = _request(
        "POST", settings.zoikonex_customer_account_url, "/v1/parties",
        json={"party_type": party_type, "pii_token": pii_token},
    )
    customer = _request(
        "POST", settings.zoikonex_customer_account_url, "/v1/customers",
        json={"party_id": party["party_id"], "customer_type": customer_type},
    )
    zn_account = _request(
        "POST", settings.zoikonex_customer_account_url, "/v1/accounts",
        json={
            "customer_id": customer["id"],
            "account_number": sub.account_id,
            "account_type": customer_type,
            "currency_code": "USD",
        },
    )

    sub.zoikonex_pii_token = pii_token
    sub.zoikonex_party_id = party["party_id"]
    sub.zoikonex_customer_id = customer["id"]
    sub.zoikonex_account_id = zn_account["id"]
    db.commit()

    return {
        "party_id": sub.zoikonex_party_id,
        "customer_id": sub.zoikonex_customer_id,
        "account_id": sub.zoikonex_account_id,
    }


# --- Usage ingestion (usage-ingestion-mediation) ---

_SOURCE_TYPE_FOR_EVENT_TYPE = {
    "call_seconds": "CDR_VOICE",
    "video_minutes": "COMMERCIAL",
    "ai_summary": "COMMERCIAL",
}


def sync_usage_event(
    db: Session, sub, usage_event_id: str, *, event_type: str, quantity: float, unit: str
) -> dict:
    """Sends one raw usage record to ZoikoNex for ingestion/normalization
    - real, tested end to end. Returns {} (no-op) if this account has
    never synced a subscription (nothing to attribute the usage to yet)
    rather than raising, since usage capture must never fail because
    billing sync hasn't happened - same non-blocking posture the mock
    this replaces already had."""
    if sub is None or sub.zoikonex_account_id is None:
        return {}

    now = _iso_now()
    result = _request(
        "POST", settings.zoikonex_usage_url, "/v1/usage/ingest",
        json={
            "source_type": _SOURCE_TYPE_FOR_EVENT_TYPE.get(event_type, "COMMERCIAL"),
            "source_system": "zoiko-local",
            "external_record_id": usage_event_id,
            "subscriber_id": sub.account_id,
            "account_id": sub.zoikonex_account_id,
            "customer_id": sub.zoikonex_customer_id,
            "usage_start_at": now,
            "usage_end_at": now,
            "quantity": quantity,
            "quantity_unit": unit.upper(),
            "raw_payload": {"event_type": event_type, "quantity": quantity, "unit": unit},
            "created_by": "zoiko-local",
        },
    )
    return {"zoikonex_ref": result.get("raw_record_id"), "status": result.get("status")}


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rate_usage_event(
    db: Session, *, event_type: str, quantity: float, unit: str, country_band: str | None
) -> dict:
    """Cost ESTIMATE only, computed from Zoiko Local's own published
    calling-rate card - NOT a real ZoikoNex rating decision. Both of
    ZoikoNex's own rating endpoints (usage-ingestion-mediation's own
    /rate, rating-charging's /v1/postpaid/rate) require the CALLER to
    supply rate_per_unit; neither looks a price up from the catalog
    automatically. Registering a real per-unit telecom rate in ZoikoNex is
    a commercial decision this codebase can't make on its own (same
    reasoning register_plan_in_catalog isn't auto-invoked) - until that
    happens, this keeps the exact same estimate the mock adapter it
    replaces already computed, purely for customer-facing visibility, not
    as a real charge. Only call_seconds has a rate table today; every
    other event_type returns no estimate rather than guessing."""
    if event_type != "call_seconds":
        return {"estimated_cost_cents": None}

    rate = None
    if country_band is not None:
        rate = db.query(CallingRate).filter(CallingRate.country == country_band).first()
    if rate is None:
        rate = db.query(CallingRate).filter(CallingRate.country == DEFAULT_RATE_COUNTRY).first()
    if rate is None:
        return {"estimated_cost_cents": None}

    minutes = math.ceil(quantity / 60)
    return {"estimated_cost_cents": minutes * rate.price_per_minute_cents}
