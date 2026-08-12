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
  Offer + PriceRule) - tested end to end. Priced from
  app.billing.models.PriceCatalogEntry, NOT a real commercial price yet -
  see that model's docstring.
- Real usage rating via rating-charging's /v1/postpaid/rate (bill-cycle
  accumulation) - tested end to end, using the placeholder-priced
  estimate from rate_usage_event as the amount, since ZoikoNex's own
  rating endpoints require the caller to supply the amount rather than
  computing it from the catalog.
- Invoice generation (billing-invoice: bill-cycle -> invoice -> line
  item -> issue) - tested end to end, including a real ZoikoNex
  Product/Offer catalog activation (DRAFT -> ACTIVE) and a real tax-
  decision call per line item (tax-jurisdiction) - see
  TAX_PLACEHOLDER_JURISDICTION_CODE's docstring for why tax always
  resolves to 0 right now.
- Payment intent creation + authorization (payments) - tested end to
  end, against the dev-only simulated payment gateway.
- Credit notes and debit notes (billing-invoice) - the only legal way to
  correct an ISSUED invoice (ZN-ADR-012 Class A) - tested end to end.
- Refunds (payments) - fully wired and tested end to end against the
  correct rejection case (a non-CAPTURED intent, 409 STATE_CONFLICT);
  can't be tested against a real success case yet since nothing in this
  environment ever reaches CAPTURED (see the capture bug below).

What's NOT working, and why - real bugs in ZoikoNex itself, not ours:
- Payment CAPTURE fails every time with a real bug in ZoikoNex's own
  code: their payments service's evidence-ledger gRPC client passes a
  request object that doesn't satisfy Go's proto.Message interface
  ("failed to marshal, message is *evidenceclient.pbAppendRequest, want
  proto.Message"), confirmed via their live error logs, not guessed.
  Not something this codebase can fix - the ZoikoNex repo wasn't
  modified. capture_payment_intent below completes intent-creation and
  authorization (both genuinely succeed) and treats capture failure as
  the same kind of degraded/retry-later outcome as every other ZoikoNex
  failure mode here, rather than raising - and since nothing ever
  reaches CAPTURED, create_refund is fully wired but has nothing to
  legally refund in this environment either.
- Bill-cycle CLOSE fails every time with a separate real bug (NULL-scan
  in their GetBillCycle SQL row-scan) - see close_bill_cycle's docstring.

verify_webhook_signature's scheme (HMAC-SHA256, `X-ZoikoNex-Signature:
sha256=<hex>`) is confirmed correct against ZoikoNex payments' own
API.INTEGRATION.md - unchanged from the mock.
"""

import base64
import hashlib
import hmac
import json
import math
import time
import uuid

import httpx
from sqlalchemy.orm import Session

from app.billing.models import Plan
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
    _token_cache["brand_id"] = _decode_jwt_claim(body["access_token"], "brand_id")
    return _token_cache["access_token"]


def _decode_jwt_claim(token: str, claim: str) -> str | None:
    """Reads one claim out of our own freshly-minted JWT without verifying
    its signature - safe here since this is a token WE just received
    directly from identity-tenancy over the same authenticated call, not
    one presented by an external caller. Used only for rating-charging's
    /v1/postpaid/rate, which - unlike every other ZoikoNex service touched
    in this module - takes brand_id as a request BODY field rather than
    deriving it from the token itself; confirmed live: an empty brand_id
    fails with "invalid input syntax for type uuid" on ZoikoNex's side
    (its bill_cycle_accumulations.brand_id column is NOT NULL with a zero-
    UUID default that an explicit empty string bypasses)."""
    payload_b64 = token.split(".")[1]
    padded = payload_b64 + "=" * (-len(payload_b64) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    return claims.get(claim)


def _get_brand_id() -> str | None:
    _get_access_token()
    return _token_cache.get("brand_id")


def _request(method: str, base_url: str, path: str, *, _allow_404: bool = False, **kwargs) -> dict | None:
    """Shared HTTP helper - attaches the Bearer token and normalizes every
    ZoikoNex service's shared error envelope (`code`/`message`/`retryable`)
    into ZoikoNexError, so callers never touch httpx directly.

    _allow_404 returns None instead of raising on a 404 - for lookups where
    "doesn't exist yet" is an expected, handled outcome (e.g.
    _ensure_tax_placeholder_policy checking for an already-ACTIVE policy),
    not a real failure."""
    token = _get_access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"

    try:
        with trace_provider_call("zoikonex", f"{method} {path}"):
            response = httpx.request(method, f"{base_url}{path}", headers=headers, timeout=20.0, **kwargs)
    except httpx.HTTPError as e:
        raise ZoikoNexError(f"ZoikoNex request to {path} failed: {e}") from e

    if _allow_404 and response.status_code == 404:
        return None
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

    Called from app.billing.service.run_billing_cycle, which resolves
    amount_minor_units from app.billing.models.PriceCatalogEntry (the
    Commercial Billing Operating Standard P0-1 "versioned APPROVED price
    catalog" - see that model's docstring) - NOT invented here. Plan
    itself still has no price fields (see Plan's docstring) - the real
    price lives in PriceCatalogEntry, and once this runs, also in
    ZoikoNex's own catalog. Re-run after a real price catalog version is
    approved by clearing the OLD registration (zoikonex_product_id back to
    NULL) for a plan before it will pick up the new price, since a
    PriceRule change is a commercial decision, not something this function
    does silently on every call.
    """
    if plan.zoikonex_product_id:
        return {
            "product_id": plan.zoikonex_product_id,
            "offer_id": plan.zoikonex_offer_id,
            "price_rule_id": plan.zoikonex_price_rule_id,
        }

    # catalog_version_id: "v1" fixed literal - see rate_usage_in_zoikonex's
    # docstring for why (caller-supplied, no real versioning feature yet).
    product = _request(
        "POST", settings.zoikonex_catalog_url, "/v1/products",
        json={
            "product_name": f"Zoiko Local - {plan.name}",
            "product_type": "POSTPAID",
            "charging_model": "OFFLINE",
            "charge_structure": "RECURRING",
            "currency_code": currency_code,
            "catalog_version_id": "v1",
        },
    )
    offer = _request(
        "POST", settings.zoikonex_catalog_url, "/v1/offers",
        json={"product_id": product["id"], "offer_name": f"{plan.name} - Standard", "catalog_version_id": "v1"},
    )
    # Product/Offer are created DRAFT (their own state machines: Product
    # DRAFT -> REVIEW -> ACTIVE..., Offer DRAFT -> ACTIVE...) - confirmed
    # live that ACTIVATE works directly from DRAFT for both, no intermediate
    # SUBMIT_FOR_REVIEW step required. Activating here rather than leaving
    # them DRAFT forever - PriceRule creation below doesn't actually require
    # either to be ACTIVE (confirmed live), but a plan real customers get
    # billed against should not sit in a permanently-unreviewed catalog state.
    # One live run showed a GET immediately after returning stale "DRAFT"
    # despite the PATCH itself returning 200 - re-checking moments later
    # showed "ACTIVE" as expected, and a from-scratch repeat of the same
    # sequence didn't reproduce it. Looks like an occasional read-after-
    # write consistency lag on ZoikoNex's side, not a failure of this call -
    # not retried here since the write itself is confirmed to have gone
    # through both times.
    _request("PATCH", settings.zoikonex_catalog_url, f"/v1/products/{product['id']}", json={"action": "ACTIVATE"})
    _request("PATCH", settings.zoikonex_catalog_url, f"/v1/offers/{offer['id']}", json={"action": "ACTIVATE"})
    price_rule = _request(
        "POST", settings.zoikonex_catalog_url, "/v1/price-rules",
        json={
            "product_id": product["id"],
            "offer_id": offer["id"],
            "amount_minor_units": amount_minor_units,
            "currency_code": currency_code,
            "charge_structure": "RECURRING",
            "billing_period": "MONTHLY",
            "catalog_version_id": "v1",
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


# Commercial Billing Operating Standard P0-8 "rating versioning" - a fixed
# literal, not a real versioning system (CallingRate has no history table;
# upsert_calling_rate mutates in place). Bump this string the day a real
# rate-history mechanism exists and old UsageEvent rows need to stay
# distinguishable from ones rated under the new logic.
CALLING_RATE_METER_VERSION = "callingrate-v1"


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


# --- Evidence anchoring (evidence-ledger) ---

def _append_evidence(*, aggregate_type: str, aggregate_id: str, producer_service: str, payload: dict) -> str:
    """Every ZoikoNex write that needs Class-A/immutable provenance
    (ZN-ADR-012) - postpaid rating and invoice issuance both require an
    evidence_id - anchors it here first via evidence-ledger's own REST
    API. This is NOT the same code path as payments' capture step (that
    goes through a broken internal gRPC client wrapper on ZoikoNex's
    side - see this module's docstring); evidence-ledger's own REST
    endpoint is a separate, correctly-implemented handler and works
    fine when called directly, confirmed live."""
    result = _request(
        "POST", settings.zoikonex_evidence_url, "/v1/evidence",
        json={
            "aggregate_type": aggregate_type,
            "aggregate_id": aggregate_id,
            "producer_service": producer_service,
            "occurred_at": _iso_now(),
            "payload": payload,
        },
    )
    return result["evidence_id"]


# --- Real usage rating (rating-charging) ---

# rating-charging has no "create bill cycle" endpoint of its own (unlike
# billing-invoice, see below) - /v1/postpaid/rate just accumulates into
# whatever bill_cycle_id the caller passes, creating the accumulation row
# on first use (confirmed live). This mints one deterministic UUID per
# subscription+period so every usage event in the same billing period
# accumulates into the same ZoikoNex-side total - a Zoiko Local-only
# grouping key, unrelated to billing-invoice's own /v1/bill-cycles
# resource (a different service's different bill-cycle concept).
_RATING_BILL_CYCLE_NAMESPACE = uuid.UUID("6a6f696b-6f2d-6c6f-6361-6c2d72617461")


def _rating_bill_cycle_id(sub) -> str:
    return str(uuid.uuid5(_RATING_BILL_CYCLE_NAMESPACE, f"{sub.id}:{sub.current_period_start.isoformat()}"))


def rate_usage_in_zoikonex(
    db: Session, sub, usage_event, *, amount_minor_units: int, currency_code: str = "USD"
) -> dict:
    """Real ZoikoNex postpaid rating (rating-charging's /v1/postpaid/rate) -
    NOT the local-only estimate in rate_usage_event above. amount_minor_units
    must come from the caller's own already-decided price (Zoiko Local's
    published CallingRate card for call_seconds) - this function only
    submits it, it does not invent or look one up. Appends an immutable
    RatedCharge on ZoikoNex's side and accumulates it into that account's
    open bill cycle total. Returns {} (no-op) if this account has never
    synced a subscription, or has no catalog registration yet (no
    product_id to attribute the charge to), matching sync_usage_event's
    posture - usage rating must never block or fail usage capture.

    catalog_version_id is a fixed "v1" literal - ZoikoNex's catalog
    versioning (product-catalogue-commercial's own docs: "every product/
    offer/price-rule carries catalog_version_id... downstream domains rate
    against the version active at event time") is caller-supplied, not
    ZoikoNex-generated, and this codebase has no real catalog-versioning
    feature yet (plans are never revised after registration - see
    register_plan_in_catalog). One fixed version until that's built.

    brand_id must be sent explicitly in the body - confirmed live that this
    endpoint (unlike every other ZoikoNex endpoint in this module) does NOT
    fall back to the JWT's own brand_id claim when it's omitted; an empty
    string fails "invalid input syntax for type uuid" on ZoikoNex's side.
    """
    if sub is None or sub.zoikonex_account_id is None or plan_product_id(db, sub) is None:
        return {}

    product_id = plan_product_id(db, sub)
    evidence_id = _append_evidence(
        aggregate_type="UsageEvent", aggregate_id=usage_event.id, producer_service="zoiko-local",
        payload={"event_type": usage_event.event_type, "amount_minor_units": amount_minor_units, "currency": currency_code},
    )
    result = _request(
        "POST", settings.zoikonex_rating_url, "/v1/postpaid/rate",
        json={
            "brand_id": _get_brand_id(),
            "subscriber_id": sub.account_id,
            "account_id": sub.zoikonex_account_id,
            "customer_id": sub.zoikonex_customer_id,
            "product_id": product_id,
            "catalog_version_id": "v1",
            "bill_cycle_id": _rating_bill_cycle_id(sub),
            "usage_type": usage_event.event_type.upper(),
            "quantity": str(usage_event.quantity),
            "quantity_unit": usage_event.unit.upper(),
            "amount_minor_units": amount_minor_units,
            "currency_code": currency_code,
            "raw_record_id": usage_event.id,
            "normalised_usage_id": usage_event.id,
            "evidence_id": evidence_id,
        },
    )
    return {"rated_charge_id": result.get("rated_charge_id"), "evidence_id": evidence_id}


def plan_product_id(db: Session, sub) -> str | None:
    """The Plan this subscription is on may not have been registered in
    ZoikoNex's catalog yet (see register_plan_in_catalog) - looked up fresh
    each call rather than cached on Subscription, since a plan change moves
    a subscription onto a different Plan row entirely."""
    plan = db.query(Plan).filter(Plan.plan_code == sub.plan_code).first()
    return plan.zoikonex_product_id if plan else None


# --- Tax (tax-jurisdiction) ---
#
# NOT REAL TAX RATES. Real sales/telecom tax rates are a legal/compliance
# decision (varies by jurisdiction, changes over time, subject to audit) -
# nobody at Zoiko has made that decision, same class of problem as
# app.billing.models.PriceCatalogEntry's placeholder prices but higher-
# stakes to guess wrong on, so this deliberately registers a single
# 0%-rate policy under a jurisdiction
# code that cannot be mistaken for a real one ("ZZ" is ISO 3166's reserved
# user-assigned/unknown-country code - never a real jurisdiction). Every
# invoice still gets a REAL tax-decision call and a REAL tax_decision_id
# from ZoikoNex - the pipeline is genuinely wired - it just always resolves
# to zero tax until Legal/Finance decides real jurisdiction policies and
# this constant is replaced.
TAX_PLACEHOLDER_JURISDICTION_CODE = "ZZ-PLACEHOLDER"


def _ensure_tax_placeholder_policy() -> None:
    """Idempotent one-time setup, analogous to register_plan_in_catalog -
    a jurisdiction must have an ACTIVE policy before any tax-decision call
    against it will succeed (confirmed live: 404 otherwise). Response field
    is `policy_id`, not `id` - confirmed live, unlike every other
    product-catalogue-commercial-style resource in this module."""
    existing = _request(
        "GET", settings.zoikonex_tax_url, "/v1/jurisdiction-policies/active",
        params={"jurisdiction_code": TAX_PLACEHOLDER_JURISDICTION_CODE},
        _allow_404=True,
    )
    if existing is not None:
        return

    # A DRAFT policy for this code may already exist from a prior partial
    # run (create succeeds, activate fails/interrupted) - list-and-reuse
    # rather than creating a duplicate DRAFT every retry.
    listing = _request(
        "GET", settings.zoikonex_tax_url, "/v1/jurisdiction-policies",
        params={"jurisdiction_code": TAX_PLACEHOLDER_JURISDICTION_CODE},
    )
    draft = next((p for p in listing.get("data", []) if p["status"] == "DRAFT"), None)

    if draft is None:
        draft = _request(
            "POST", settings.zoikonex_tax_url, "/v1/jurisdiction-policies",
            json={
                "jurisdiction_code": TAX_PLACEHOLDER_JURISDICTION_CODE,
                "country_code": "ZZ",
                "provider_name": "MANUAL",
                "policy_version": "placeholder-v1-zero-rate",
                "currency_code": "USD",
                "tax_category_map": {
                    "DEFAULT": {"components": [{"name": "PLACEHOLDER_NOT_REAL_TAX", "rate_bps": 0}]},
                },
            },
        )
    _request("POST", settings.zoikonex_tax_url, f"/v1/jurisdiction-policies/{draft['policy_id']}/activate")


def determine_tax_for_invoice_line(*, invoice_id: str, taxable_amount_minor_units: int, currency_code: str = "USD") -> dict:
    """Real ZoikoNex tax determination (tax-jurisdiction's /v1/tax-decisions)
    - genuinely wired, but always resolves to 0 tax against
    TAX_PLACEHOLDER_JURISDICTION_CODE's 0%% policy until real jurisdiction
    policies exist (see that constant's docstring). Returns {} (no-op,
    tax_amount_minor_units left unset) rather than raising on failure -
    tax determination must never block invoicing, same non-blocking
    posture as every other ZoikoNex call in this module."""
    try:
        _ensure_tax_placeholder_policy()
        result = _request(
            "POST", settings.zoikonex_tax_url, "/v1/tax-decisions",
            json={
                "aggregate_type": "Invoice",
                "aggregate_id": invoice_id,
                "jurisdiction_code": TAX_PLACEHOLDER_JURISDICTION_CODE,
                "tax_category": "DEFAULT",
                "taxable_amount_minor_units": taxable_amount_minor_units,
                "currency_code": currency_code,
            },
        )
    except ZoikoNexError:
        return {}
    return {
        "tax_decision_id": result.get("tax_decision_id"),
        "tax_amount_minor_units": result.get("tax_amount_minor_units"),
    }


# --- Invoicing (billing-invoice) ---
#
# Confirmed live: bill-cycle/invoice/line-item bodies come back
# lowercase/snake_case like every other ZoikoNex service in this module -
# EXCEPT /v1/invoices/{id}/issue specifically, which mirrors the Go
# aggregate directly in PascalCase (InvoiceID, TotalMinorUnits, Status,
# ...). The API.INTEGRATION.md claim that ALL billing-invoice responses
# are PascalCase does not hold for bill-cycles/invoices/line-items -
# don't trust that doc's casing claim beyond /issue without re-checking.
#
# Also confirmed live but undocumented: line-items and issue both require
# an Idempotency-Key header (400 MISSING_IDEMPOTENCY_KEY without one),
# even though the endpoint table only calls it out for bill-cycles/
# invoices creation.

def open_bill_cycle(sub) -> dict:
    result = _request(
        "POST", settings.zoikonex_billing_url, "/v1/bill-cycles",
        headers={"Idempotency-Key": f"bill-cycle-{sub.id}-{sub.current_period_start.date()}"},
        json={"account_id": sub.zoikonex_account_id, "customer_id": sub.zoikonex_customer_id},
    )
    return {"bill_cycle_id": result["id"], "status": result["status"]}


def close_bill_cycle(bill_cycle_id: str) -> dict:
    """Confirmed broken on ZoikoNex's side right now, independent of the
    payments/evidence-ledger bug documented elsewhere in this module: both
    this endpoint and plain `GET /v1/bill-cycles/{id}` call the same
    internal GetBillCycle repository function, which fails on every bill
    cycle created in this environment with "can't scan into dest[12]:
    cannot scan NULL into *string" - a genuine NULL-handling bug in their
    SQL row scan (some nullable column, likely jurisdiction_code given it
    comes back as "" rather than never being set), not something fixable
    from here. Raises ZoikoNexError like any other failed call - callers
    (see app.billing.service.run_billing_cycle) must treat a failed close
    as non-blocking, the same posture as a failed payment capture."""
    result = _request(
        "POST", settings.zoikonex_billing_url, f"/v1/bill-cycles/{bill_cycle_id}/close",
        headers={"Idempotency-Key": f"bill-cycle-close-{bill_cycle_id}"},
    )
    return {"status": result["status"]}


def create_invoice(sub, bill_cycle_id: str, *, currency_code: str = "USD") -> dict:
    result = _request(
        "POST", settings.zoikonex_billing_url, "/v1/invoices",
        headers={"Idempotency-Key": f"invoice-{sub.id}-{sub.current_period_start.date()}"},
        json={
            "account_id": sub.zoikonex_account_id,
            "customer_id": sub.zoikonex_customer_id,
            "currency_code": currency_code,
            "bill_cycle_id": bill_cycle_id,
        },
    )
    return {"invoice_id": result["id"], "status": result["status"]}


def get_invoice(invoice_id: str) -> dict:
    """A LIVE read of the invoice's current status - confirmed live that
    create_invoice's idempotent replay does NOT do this: calling
    POST /v1/invoices again with the same Idempotency-Key returns the
    exact response body captured at first-creation time (status: "DRAFT"
    forever), even after the invoice has since been issued. Callers that
    need to know whether an invoice from a prior run was already issued
    (see app.billing.service.run_billing_cycle) must call this, not trust
    create_invoice's return value on a repeat call.

    Also confirmed live: this endpoint's response shape itself is
    inconsistent depending on state - a flat invoice object
    (`{id, status, ...}`) when the invoice has NO line items yet, but
    `{invoice: {...}, line_items: [...]}` once at least one exists. Handles
    both rather than assuming the docs' `{invoice, line_items}` shape
    always applies."""
    result = _request("GET", settings.zoikonex_billing_url, f"/v1/invoices/{invoice_id}")
    invoice = result.get("invoice", result)
    return {"invoice_id": invoice["id"], "status": invoice["status"], "total_minor_units": invoice.get("total_minor_units")}


def add_invoice_line_item(
    invoice_id: str, *, description: str, amount_minor_units: int, quantity: int = 1,
    tax_amount_minor_units: int | None = None, line_key: str = "default",
) -> dict:
    """line_key (not the free-text description) forms the Idempotency-Key -
    a description can contain characters HTTP headers reject outright
    (confirmed live: httpx/h11 raised LocalProtocolError on one containing
    a comma inside parentheses), so it must never be embedded in a
    header value."""
    body = {"line_description": description, "unit_amount_minor_units": amount_minor_units, "quantity": quantity}
    if tax_amount_minor_units is not None:
        body["tax_amount_minor_units"] = tax_amount_minor_units
    result = _request(
        "POST", settings.zoikonex_billing_url, f"/v1/invoices/{invoice_id}/line-items",
        headers={"Idempotency-Key": f"invoice-line-{invoice_id}-{line_key}"},
        json=body,
    )
    return {"line_item_id": result["id"]}


def issue_invoice(invoice_id: str) -> dict:
    """DRAFT -> PENDING_APPROVAL -> ISSUED (issue auto-submits a DRAFT).
    Once ISSUED, monetary fields are permanently immutable on ZoikoNex's
    side (ZN-ADR-012 Class A) - corrections after this point are credit/
    debit notes, never edits. evidence_id is left unset - confirmed live
    that billing-invoice mints its own EvidenceID internally when the
    request body omits one, so there's no need for this module to append
    one itself first (unlike rating-charging's postpaid/rate, where
    evidence_id really is a mandatory caller-supplied field)."""
    result = _request(
        "POST", settings.zoikonex_billing_url, f"/v1/invoices/{invoice_id}/issue",
        headers={"Idempotency-Key": f"invoice-issue-{invoice_id}"},
        json={},
    )
    return {"status": result["Status"], "total_minor_units": result.get("TotalMinorUnits")}


def create_credit_note(
    invoice_id: str, *, reason_code: str, amount_minor_units: int, reason_description: str | None = None,
    idempotency_key: str,
) -> dict:
    """Corrects an over-billed ISSUED invoice - the only legal correction
    mechanism once ISSUED (ZN-ADR-012 Class A means the invoice itself can
    never be edited). 422 if the invoice isn't ISSUED yet (confirmed by the
    docs, matches every other Class-A immutability check in this module).
    Idempotency-Key header required - confirmed live, undocumented for this
    endpoint specifically (the docs only call it out for bill-cycles/
    invoice creation)."""
    body = {"reason_code": reason_code, "amount_minor_units": amount_minor_units}
    if reason_description is not None:
        body["reason_description"] = reason_description
    result = _request(
        "POST", settings.zoikonex_billing_url, f"/v1/invoices/{invoice_id}/credit-notes",
        headers={"Idempotency-Key": idempotency_key}, json=body,
    )
    return {"credit_note_id": result["id"], "status": result["status"], "amount_minor_units": result["amount_minor_units"]}


def create_debit_note(
    invoice_id: str, *, reason_code: str, amount_minor_units: int, idempotency_key: str,
) -> dict:
    """Corrects an under-billed ISSUED invoice - see create_credit_note's
    docstring for the same Class-A rationale; same undocumented-but-
    required Idempotency-Key header, confirmed live."""
    result = _request(
        "POST", settings.zoikonex_billing_url, f"/v1/invoices/{invoice_id}/debit-notes",
        headers={"Idempotency-Key": idempotency_key},
        json={"reason_code": reason_code, "amount_minor_units": amount_minor_units},
    )
    return {"debit_note_id": result["id"], "status": result["status"], "amount_minor_units": result["amount_minor_units"]}


# --- Payment collection (payments) ---
#
# See this module's docstring: intent creation + authorise are real and
# tested; capture fails every time against a genuine bug in ZoikoNex's own
# payments<->evidence-ledger gRPC wrapper, not something fixable from here.

class ZoikoNexCaptureFailedError(Exception):
    """Raised (and expected to be caught) when payment capture fails -
    carries the ZoikoNex-side error message for logging/reporting, so a
    caller can record "authorized but not captured" instead of silently
    treating it as fully collected."""


def create_payment_intent(
    sub, invoice_id: str, *, amount_minor_units: int, currency_code: str = "USD"
) -> dict:
    result = _request(
        "POST", settings.zoikonex_payments_url, "/v1/payment-intents",
        headers={"Idempotency-Key": f"pay-intent-{invoice_id}"},
        json={
            "invoice_id": invoice_id,
            "account_id": sub.zoikonex_account_id,
            "customer_id": sub.zoikonex_customer_id,
            "payment_rail": "CARD",
            # "STRIPE" here does NOT mean a real Stripe call - payments
            # boots with PAYMENTS_ALLOW_SIMULATED_GATEWAY=true in this dev
            # stack (docker-compose.override.yml on the ZoikoNex side),
            # which no-ops every provider call regardless of provider_name.
            # Confirmed live that an unrecognized provider_name (tried
            # "SIMULATED") causes an unhandled internal error on authorise -
            # this service's routing apparently expects a real rail-provider
            # name even when the underlying gateway is faked.
            "provider_name": "STRIPE",
            "amount_minor_units": amount_minor_units,
            "currency_code": currency_code,
            # PCI boundary (ZN-ADR): only a tokenised method reference is ever
            # sent - real card data is never accepted or stored anywhere in
            # this codebase. This is the dev-only simulated-gateway test token.
            "payment_method_token": "pm_test_card",
        },
    )
    return {"payment_intent_id": result["payment_intent_id"], "status": result.get("status")}


def authorise_payment_intent(payment_intent_id: str) -> dict:
    """Idempotency-Key is required here too - confirmed live, and
    undocumented in payments' own API.INTEGRATION.md (which only calls it
    "mandatory" for intent creation, not authorise/capture)."""
    result = _request(
        "POST", settings.zoikonex_payments_url, f"/v1/payment-intents/{payment_intent_id}/authorise",
        headers={"Idempotency-Key": f"pay-authorise-{payment_intent_id}"},
    )
    return {"status": result.get("status")}


def capture_payment_intent(payment_intent_id: str) -> dict:
    """Confirmed broken on ZoikoNex's side right now (evidence-ledger gRPC
    marshaling bug - see this module's docstring) - raises
    ZoikoNexCaptureFailedError rather than ZoikoNexError specifically so
    callers can tell "the whole ZoikoNex call failed" (network/auth/etc,
    handled the normal way) apart from "capture itself was rejected by
    ZoikoNex" (a distinct, already-diagnosed, known failure mode)."""
    try:
        result = _request(
            "POST", settings.zoikonex_payments_url, f"/v1/payment-intents/{payment_intent_id}/capture",
            headers={"Idempotency-Key": f"pay-capture-{payment_intent_id}"},
        )
    except ZoikoNexError as e:
        raise ZoikoNexCaptureFailedError(str(e)) from e
    return {"status": result.get("status")}


def create_refund(
    payment_intent_id: str, *, refund_amount_minor_units: int, currency_code: str = "USD",
    reason_code: str, idempotency_key: str,
) -> dict:
    """Refunds a CAPTURED payment (full or partial) - confirmed live that
    ZoikoNex correctly rejects this with 409 STATE_CONFLICT against a
    non-CAPTURED intent (e.g. only AUTHORISED), rather than a crash - so
    given capture is currently broken on ZoikoNex's side (see this
    module's docstring), this function is fully wired and correct but has
    nothing to legally refund yet in this environment.

    idempotency_key is BOTH an Idempotency-Key header AND a body field -
    confirmed live that the header is required (undocumented; the docs
    only show the body field) in addition to the documented body field."""
    result = _request(
        "POST", settings.zoikonex_payments_url, f"/v1/payment-intents/{payment_intent_id}/refunds",
        headers={"Idempotency-Key": idempotency_key},
        json={
            "refund_amount_minor_units": refund_amount_minor_units, "currency_code": currency_code,
            "reason_code": reason_code, "idempotency_key": idempotency_key,
        },
    )
    return {"refund_id": result.get("id") or result.get("refund_id"), "status": result.get("status")}
