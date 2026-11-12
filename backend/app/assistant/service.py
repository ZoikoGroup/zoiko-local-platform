"""Public website assistant service (WF-01..WF-13).

Fixed processing order per the wireframes - this ordering IS the safety
model, and handle_message is the only entry point that must preserve it:

1. WF-13 urgent-path classifier (data-driven regex, AssistantSafetyRule
   category=urgent) - outranks everything; any hit returns the static
   safety script with no flow continuation and no inventory claims.
2. WF-10 sensitive-input redaction (category=sensitive) - the message is
   discarded unprocessed and unstored; only a metadata-only audit event
   records that a block happened (never the text).
3. Intent resolution - explicit intent_id (quick-start replay) wins, else
   deterministic keyword classification from the AssistantIntent registry
   (sort_order defines precedence), else the unknown fallback.
4. Intent handlers compose a WF-11 response: claims built ONLY from
   AssistantKnowledgeSource rows (live data answers - prices, market
   status - additionally read the approved catalog/market registry tables
   and cite their product-page source row), actions resolved ONLY through
   the AssistantDestination allowlist.

Privacy by design (WF-10): no chat text is ever written to any table -
transcripts live in the visitor's browser only; this module persists just
the session row (validated enums, counters, timestamps) and audit events
(metadata only). Audit coverage therefore deliberately stops at the
state-changing actions the repo rule asks for (session create/end, the
two policy-block events) - per-message counter bumps are telemetry, not
business state, and logging every message would itself violate WF-10's
"no raw free-text in analytics" rule.
"""

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.assistant import content
from app.assistant.models import (
    AssistantDestination,
    AssistantIntent,
    AssistantKnowledgeSource,
    AssistantSafetyCategory,
    AssistantSafetyRule,
    AssistantSession,
)
from app.assistant.schemas import AssistantMessageResponse
from app.audit.service import log_event
from app.billing.models import (
    AIReceptionistAddonRate,
    BillingPeriod,
    CatalogEntryStatus,
    Plan,
    PriceCatalogEntry,
)
from app.compliance.models import ComplianceRule
from app.core.ids import new_uuid
from app.integrations.cache.redis import cache_get, cache_set
from app.numbering.numbers.models import (
    MarketActivationStatus,
    NumberEligibilityRule,
    SupportedCountry,
)

logger = logging.getLogger(__name__)

_BOOTSTRAP_CACHE_KEY = "assistant:bootstrap:v1"
# Short TTL + explicit invalidation isn't possible for this data (there
# is no staff write path yet - content changes come via migrations), so
# this only bounds drift after a deploy/seed, same posture as careers'
# 30s public-jobs cache.
_BOOTSTRAP_CACHE_TTL_SECONDS = 60

# Publicly-operating market states (ZL-COM-LAUNCH-001): OPEN is public,
# RESTRICTED is public-with-restrictions, LAUNCHING is explicitly not
# public yet. The country chips the assistant offers (WF-02's coverage
# plane) come from these - never from "the provider happens to have
# numbers there".
_PUBLIC_MARKET_STATUSES = {MarketActivationStatus.OPEN, MarketActivationStatus.RESTRICTED}

_VALID_CUSTOMER_TYPES = {"business", "personal"}

_ANON_ACTOR = "anonymous_public_visitor"


class SessionNotFoundError(Exception):
    pass


class SessionExpiredError(Exception):
    pass


class SessionEndedError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Bootstrap (WF-01)
# ---------------------------------------------------------------------------

def get_bootstrap(db: Session) -> dict:
    """Everything the widget needs before any session exists. The
    DB-derived half is cached (registry reads are 3 queries otherwise);
    the kill switch (`enabled`) is deliberately OUTSIDE the cache so
    settings.public_assistant_enabled flips take effect immediately."""
    cached = cache_get(_BOOTSTRAP_CACHE_KEY)
    if cached is None:
        intents = (
            db.query(AssistantIntent)
            .filter(AssistantIntent.is_active.is_(True), AssistantIntent.is_quick_start.is_(True))
            .order_by(AssistantIntent.sort_order)
            .all()
        )
        privacy = _destination_row(db, "PRIVACY_POLICY")
        cached = {
            "assistant_name": content.ASSISTANT_NAME,
            "ai_disclosure": content.AI_DISCLOSURE,
            "safety_notice": content.SAFETY_NOTICE,
            "content_version": content.CONTENT_VERSION,
            "supported_locales": list(content.SUPPORTED_LOCALES),
            "default_locale": content.DEFAULT_LOCALE,
            "privacy_url": privacy.url if privacy else "/zoiko-local-privacy-policy",
            "actions": [
                {"intent_id": i.intent_id, "title": i.title, "description": i.description}
                for i in intents
            ],
        }
        cache_set(_BOOTSTRAP_CACHE_KEY, cached, ttl_seconds=_BOOTSTRAP_CACHE_TTL_SECONDS)
    return {**cached, "enabled": True}


# ---------------------------------------------------------------------------
# Session lifecycle (WF-01 / WF-10)
# ---------------------------------------------------------------------------

def create_session(db: Session, *, page_context: str | None = None, locale: str | None = None) -> dict:
    locale_fallback = locale is not None and locale not in content.SUPPORTED_LOCALES
    token = secrets.token_urlsafe(32)
    row = AssistantSession(
        session_token_hash=_hash_token(token),
        page_context=page_context,
        locale=content.DEFAULT_LOCALE if locale_fallback or not locale else locale,
        flow_context={},
        expires_at=_now() + timedelta(minutes=content.SESSION_TTL_MINUTES),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    log_event(
        db,
        actor=_ANON_ACTOR,
        action="assistant.session_created",
        target=f"assistant_session:{row.id}",
        after={"page_context": row.page_context, "locale": row.locale, "expires_at": row.expires_at.isoformat()},
    )
    return {
        "session_id": row.id,
        "session_token": token,
        "expires_at": row.expires_at.isoformat(),
        "locale_used": row.locale,
        "locale_fallback": locale_fallback,
    }


def authenticate_session(db: Session, *, session_id: str, session_token: str) -> AssistantSession:
    """Constant-time token comparison (the hash is the secret's only
    stored form - comparing hashes with hmac.compare_digest, not ==,
    keeps the lookup timing-uniform). Raises typed errors the route maps
    to 401/410 so a stale widget gets a precise 'create a new session'
    signal rather than a generic failure."""
    row = db.query(AssistantSession).filter(AssistantSession.id == session_id).first()
    if row is None or not hmac.compare_digest(row.session_token_hash, _hash_token(session_token)):
        raise SessionNotFoundError()
    if row.ended_at is not None:
        raise SessionEndedError()
    if row.expires_at <= _now():
        raise SessionExpiredError()
    return row


def end_session(db: Session, *, session_id: str, session_token: str) -> dict:
    row = authenticate_session(db, session_id=session_id, session_token=session_token)
    row.ended_at = _now()
    db.commit()
    log_event(
        db,
        actor=_ANON_ACTOR,
        action="assistant.session_ended",
        target=f"assistant_session:{row.id}",
        after={"message_count": row.message_count},
    )
    return {"ended": True, "session_id": row.id}


def purge_expired_sessions(db: Session) -> dict:
    """Daily sweep (wired into ops/scheduled_reconciliation). Deletes
    sessions whose TTL passed (or that were ended) more than
    SESSION_PURGE_GRACE_HOURS ago - the grace exists so a just-expired
    session's 401 responses still have a row to be rejected against;
    after that, data minimization says the row shouldn't exist at all."""
    cutoff = _now() - timedelta(hours=content.SESSION_PURGE_GRACE_HOURS)
    deleted = (
        db.query(AssistantSession)
        .filter(
            (AssistantSession.expires_at < cutoff)
            | ((AssistantSession.ended_at.isnot(None)) & (AssistantSession.ended_at < cutoff))
        )
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"deleted": deleted}


# ---------------------------------------------------------------------------
# Message handling (WF-01/02/10/11/13)
# ---------------------------------------------------------------------------

def handle_message(
    db: Session,
    session: AssistantSession,
    *,
    intent_id: str | None = None,
    text: str | None = None,
    selection: dict | None = None,
    client_message_id: str | None = None,
) -> AssistantMessageResponse:
    session.message_count += 1
    session.last_seen_at = _now()

    # 1) WF-13 urgent path - deterministic, first, and terminal.
    if text and _matches_safety_category(db, AssistantSafetyCategory.URGENT, text):
        log_event(
            db,
            actor=_ANON_ACTOR,
            action="assistant.urgent_path_triggered",
            target=f"assistant_session:{session.id}",
            after={"message_number": session.message_count},
        )
        response = _build_response(
            db=db,
            response_type="safety_urgent",
            text=content.URGENT_RESPONSE_TEXT,
            actions=[("EMERGENCY_CALLING", "primary")],
            verifiability_status="none",
        )

    # 2) WF-10 sensitive input - discard before any storage or routing.
    elif text and _matches_safety_category(db, AssistantSafetyCategory.SENSITIVE, text):
        log_event(
            db,
            actor=_ANON_ACTOR,
            action="assistant.sensitive_input_blocked",
            target=f"assistant_session:{session.id}",
            after={"message_number": session.message_count},
        )
        response = _build_response(
            db=db,
            response_type="sensitive_blocked",
            text=content.SENSITIVE_RESPONSE_TEXT,
            actions=[],
            verifiability_status="none",
        )

    else:
        intent = _resolve_intent(db, intent_id=intent_id, text=text)
        if intent is None:
            # Unknown text, unknown/stale intent_id, or no usable input
            # at all - honest non-comprehension, never a guess (WF-01).
            response = _unknown(db, session)
        elif intent.intent_id == "NUMBER_DISCOVERY":
            # The discovery flow also accepts a typed selection payload
            # alongside an explicit chip/text trigger (WF-02).
            response = _number_discovery(db, session, selection=selection)
        else:
            response = _INTENT_HANDLERS[intent.intent_id](db, session)

    db.commit()
    response.client_message_id = client_message_id
    response.locale_used = session.locale
    return response


# ---------------------------------------------------------------------------
# Safety + intent classification helpers
# ---------------------------------------------------------------------------

def _matches_safety_category(db: Session, category: AssistantSafetyCategory, text: str) -> bool:
    """Compiles the active patterns for a category fresh each call and
    returns whether any matches - same load-and-compile approach as
    intelligence.guardrails.load_active_guardrail_patterns (this is a
    cold public path but the tables are tiny; correctness beats a cache
    here). Invalid regex rows are skipped, never fatal."""
    rules = (
        db.query(AssistantSafetyRule)
        .filter(AssistantSafetyRule.category == category, AssistantSafetyRule.is_active.is_(True))
        .order_by(AssistantSafetyRule.sort_order)
        .all()
    )
    for rule in rules:
        try:
            if re.search(rule.pattern, text, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False


def _resolve_intent(db: Session, *, intent_id: str | None, text: str | None) -> AssistantIntent | None:
    """Explicit intent_id (quick-start chip replay) wins outright when it
    names an active intent - a stale/unknown id degrades to the unknown
    fallback rather than erroring, so a widget running older bootstrap
    content can't hard-fail. Otherwise free text is classified
    deterministically against the keyword registry (sort_order defines
    precedence: porting/example phrases are checked before the generic
    number/receptionist phrases they contain)."""
    intents = (
        db.query(AssistantIntent)
        .filter(AssistantIntent.is_active.is_(True))
        .order_by(AssistantIntent.sort_order)
        .all()
    )
    if intent_id:
        for intent in intents:
            if intent.intent_id == intent_id:
                return intent
        return None
    if text:
        lowered = text.lower()
        for intent in intents:
            for keyword in (intent.keywords or []):
                if re.search(rf"\b{re.escape(keyword.lower())}\b", lowered):
                    return intent
    return None


# ---------------------------------------------------------------------------
# Knowledge/claims helpers (WF-11)
# ---------------------------------------------------------------------------

def _knowledge_rows(db: Session, intent_id: str) -> list[AssistantKnowledgeSource]:
    return (
        db.query(AssistantKnowledgeSource)
        .filter(
            AssistantKnowledgeSource.intent_id == intent_id,
            AssistantKnowledgeSource.is_active.is_(True),
        )
        .order_by(AssistantKnowledgeSource.created_at)
        .all()
)


def _claims_from_rows(rows: list[AssistantKnowledgeSource]) -> tuple[list[dict], list[dict]]:
    """Every seeded row is exactly one claim citing itself (WF-11: no
    uncited claims, no unshown citations). Dynamic claim builders below
    reuse the same shape, always naming real slugs."""
    claims = [{"text": row.body, "source_slugs": [row.slug]} for row in rows]
    sources = [
        {
            "slug": row.slug,
            "title": row.title,
            "publisher": row.publisher,
            "canonical_url": row.canonical_url,
            "authority_class": row.authority_class.value,
            "content_version": row.content_version,
            "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        }
        for row in rows
    ]
    return claims, sources


def _destination_row(db: Session, destination_key: str) -> AssistantDestination | None:
    return (
        db.query(AssistantDestination)
        .filter(AssistantDestination.destination_key == destination_key, AssistantDestination.is_active.is_(True))
        .first()
    )


def _build_response(
    *,
    response_type: str,
    text: str,
    claims: list[dict] | None = None,
    sources: list[dict] | None = None,
    actions: list[tuple[str, str]] | None = None,
    suggestions: list[dict] | None = None,
    intent_id: str | None = None,
    flow_step: str | None = None,
    verifiability_status: str = "qualified",
    db: Session | None = None,
) -> AssistantMessageResponse:
    """Assembles the WF-11 envelope. `actions` arrive as
    (destination_key, style) tuples so callers can never smuggle a URL
    in - each is resolved through the allowlist, and a missing/inactive
    destination row drops the action entirely (never a fallback URL)."""
    resolved_actions = []
    if db is not None:
        for destination_key, style in actions or []:
            row = _destination_row(db, destination_key)
            if row is not None:
                resolved_actions.append(
                    {"destination_key": row.destination_key, "label": row.label, "url": row.url, "style": style}
                )
    return AssistantMessageResponse(
        message_id=new_uuid(),
        response_type=response_type,
        intent_id=intent_id,
        text=text,
        claims=claims or [],
        sources=sources or [],
        actions=resolved_actions,
        suggestions=suggestions or [],
        flow_step=flow_step,
        verifiability_status=verifiability_status,
        locale_used=content.DEFAULT_LOCALE,
        content_version=content.CONTENT_VERSION,
    )


def _quick_start_suggestions(db: Session) -> list[dict]:
    intents = (
        db.query(AssistantIntent)
        .filter(AssistantIntent.is_active.is_(True), AssistantIntent.is_quick_start.is_(True))
        .order_by(AssistantIntent.sort_order)
        .all()
    )
    return [{"label": i.title, "value": i.intent_id, "kind": "intent"} for i in intents]


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------

def _number_discovery(db: Session, session: AssistantSession, *, selection: dict | None) -> AssistantMessageResponse:
    """WF-02 guided flow: country -> business/personal -> grounded
    summary. flow_context carries only validated values (ISO country
    code from SupportedCountry, 'business'/'personal'); a country that
    isn't in the market registry is answered honestly as not-covered
    rather than guessed. Inventory is NEVER claimed - WF-02's inventory
    plane is only ever filled by the real purchase flow's signed provider
    response, which this assistant deliberately does not make."""
    flow = dict(session.flow_context or {})
    claims, sources = _claims_from_rows(_knowledge_rows(db, "NUMBER_DISCOVERY"))

    country_code = (selection or {}).get("country")
    customer_type = (selection or {}).get("customer_type")

    if country_code is not None:
        country = db.query(SupportedCountry).filter(SupportedCountry.code == country_code.upper()).first()
        if country is None:
            return _build_response(
                db=db,
                response_type="guidance",
                intent_id="NUMBER_DISCOVERY",
                flow_step="country",
                text=(
                    f"I don't have {country_code.upper()} in the current coverage list. "
                    "Here are the countries Zoiko Local operates in today - coverage changes "
                    "over time, so check the Global Coverage page for the current list."
                ),
                claims=claims,
                sources=sources,
                actions=[("GLOBAL_COVERAGE", "primary"), ("CONTACT_SALES", "secondary")],
                suggestions=_country_suggestions(db),
                verifiability_status="qualified",
            )
        flow["country"] = country.code

    if customer_type is not None and customer_type.lower() in _VALID_CUSTOMER_TYPES:
        flow["customer_type"] = customer_type.lower()
    session.flow_context = flow

    if "country" not in flow:
        return _build_response(
            db=db,
            response_type="guidance",
            intent_id="NUMBER_DISCOVERY",
            flow_step="country",
            text=(
                "I can help you explore local numbers. Which country do you need a local "
                "presence in? I'll share what's covered, what verification applies, and "
                "the next step - I can't check live inventory from chat."
            ),
            claims=claims,
            sources=sources,
            actions=[("GLOBAL_COVERAGE", "secondary")],
            suggestions=_country_suggestions(db),
            verifiability_status="qualified",
        )

    country = db.query(SupportedCountry).filter(SupportedCountry.code == flow["country"]).first()

    if "customer_type" not in flow:
        return _build_response(
            db=db,
            response_type="guidance",
            intent_id="NUMBER_DISCOVERY",
            flow_step="customer_type",
            text=(
                f"Got it - {country.name}. Are you exploring this for a business or for "
                "personal use?"
            ),
            claims=[],
            sources=[],
            suggestions=[
                {"label": "A business", "value": "business", "kind": "customer_type"},
                {"label": "Personal use", "value": "personal", "kind": "customer_type"},
            ],
            verifiability_status="none",
        )

    # --- summary step: grounded in the market/eligibility registries ---
    dynamic_claims: list[dict] = []
    if country.market_status in _PUBLIC_MARKET_STATUSES:
        dynamic_claims.append({
            "text": (
                f"Zoiko Local currently operates in {country.name}, with market status "
                f"'{country.market_status.value}' - you can browse its number types from "
                "the Get a Local Number page."
            ),
            "source_slugs": ["global-coverage-page"],
        })
    else:
        dynamic_claims.append({
            "text": (
                f"{country.name} is not a publicly open Zoiko Local market yet (current "
                "status: launching or restricted) - I can't speak to availability there."
            ),
            "source_slugs": ["global-coverage-page"],
        })

    eligibility_rules = (
        db.query(NumberEligibilityRule)
        .filter(NumberEligibilityRule.country == country.code, NumberEligibilityRule.is_active.is_(True))
        .all()
    )
    evidence_requiring = [r for r in eligibility_rules if r.required_evidence]
    if evidence_requiring:
        types = ", ".join(sorted({r.number_type for r in evidence_requiring}))
        evidence = ", ".join(sorted({e for r in evidence_requiring for e in (r.required_evidence or [])}))
        dynamic_claims.append({
            "text": (
                f"Some number types there ({types}) may require verification evidence "
                f"({evidence}) before activation."
            ),
            "source_slugs": ["verification-help-overview"],
        })
    compliance_rules = (
        db.query(ComplianceRule)
        .filter(ComplianceRule.country == country.code, ComplianceRule.is_active.is_(True))
        .all()
    )
    if compliance_rules:
        requirement_types = ", ".join(sorted({r.requirement_type for r in compliance_rules}))
        dynamic_claims.append({
            "text": (
                f"Account-level verification may also apply in {country.name} "
                f"({requirement_types}) - the exact checks are confirmed during signup."
            ),
            "source_slugs": ["verification-help-overview"],
        })
    if not evidence_requiring and not compliance_rules:
        dynamic_claims.append({
            "text": (
                f"Verification requirements for {country.name} are confirmed during signup "
                "and checkout for your specific selection."
            ),
            "source_slugs": ["verification-help-overview"],
        })

    audience = "for a business" if flow["customer_type"] == "business" else "for personal use"
    closing = (
        f"I can't confirm live number availability from chat - current inventory and "
        f"final costs for {country.name} ({audience}) are shown during search and secure "
        "checkout."
    )
    all_claims = claims + dynamic_claims
    summary_text = " ".join(claim["text"] for claim in all_claims) + " " + closing

    coverage_row = next(
        (s for s in _knowledge_rows(db, "NUMBER_DISCOVERY") if s.slug == "global-coverage-page"), None
    )
    extra_sources = []
    if coverage_row is not None:
        _, extra = _claims_from_rows([coverage_row])
        extra_sources = extra

    return _build_response(
        db=db,
        response_type="answer",
        intent_id="NUMBER_DISCOVERY",
        flow_step="summary",
        text=summary_text,
        claims=all_claims,
        sources=sources + extra_sources,
        actions=[("GET_LOCAL_NUMBER", "primary"), ("CONTACT_SALES", "secondary")],
        suggestions=[
            {"label": "Explore another country", "value": "NUMBER_DISCOVERY", "kind": "intent"},
            {"label": "How calling works", "value": "CALLING_OVERVIEW", "kind": "intent"},
        ],
        verifiability_status="qualified",
    )


def _country_suggestions(db: Session) -> list[dict]:
    """WF-02 coverage plane: countries from the market registry whose
    status is publicly operating - never inferred from provider reach."""
    countries = (
        db.query(SupportedCountry)
        .filter(SupportedCountry.market_status.in_(_PUBLIC_MARKET_STATUSES))
        .order_by(SupportedCountry.sort_order, SupportedCountry.name)
        .all()
    )
    suggestions = [{"label": c.name, "value": c.code, "kind": "country"} for c in countries]
    suggestions.append({"label": "Something else", "value": "CONTACT_HANDOFF", "kind": "intent"})
    return suggestions


def _receptionist_overview(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    claims, sources = _claims_from_rows(_knowledge_rows(db, "RECEPTIONIST_OVERVIEW"))
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="RECEPTIONIST_OVERVIEW",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("AI_RECEPTIONIST", "primary")],
        suggestions=[{"label": "Show me an example", "value": "RECEPTIONIST_EXAMPLE", "kind": "intent"}],
        verifiability_status="qualified",
    )


def _receptionist_example(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    claims, sources = _claims_from_rows(_knowledge_rows(db, "RECEPTIONIST_EXAMPLE"))
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="RECEPTIONIST_EXAMPLE",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("AI_RECEPTIONIST", "primary")],
        suggestions=[{"label": "How the receptionist works", "value": "RECEPTIONIST_OVERVIEW", "kind": "intent"}],
        verifiability_status="qualified",
    )


def _calling_overview(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    claims, sources = _claims_from_rows(_knowledge_rows(db, "CALLING_OVERVIEW"))
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="CALLING_OVERVIEW",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("CLOUD_CALLING", "primary"), ("START_FREE", "secondary")],
        verifiability_status="qualified",
    )


def _plans_overview(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    """WF-06: plan names and monthly prices come ONLY from the approved,
    ACTIVE, non-placeholder price catalog (joined to Plan for the
    included entitlements) - never invented, never totaled. Number
    rental, usage and taxes are named as separate line items that only
    checkout computes. With no visible ACTIVE rows the answer degrades
    to a pointer to the pricing page instead of fabricating anything."""
    rows = (
        db.query(PriceCatalogEntry, Plan)
        .join(Plan, Plan.plan_code == PriceCatalogEntry.plan_code)
        .filter(
            PriceCatalogEntry.status == CatalogEntryStatus.ACTIVE,
            PriceCatalogEntry.billing_period == BillingPeriod.MONTHLY,
            PriceCatalogEntry.market == "GLOBAL",
            PriceCatalogEntry.is_placeholder.is_(False),
        )
        .order_by(Plan.sort_order)
        .all()
    )
    knowledge_claims, sources = _claims_from_rows(_knowledge_rows(db, "PLANS_OVERVIEW"))

    if not rows:
        return _build_response(
            db=db,
            response_type="answer",
            intent_id="PLANS_OVERVIEW",
            text=(
                "I can't see approved plan prices right now, so I won't guess. Current "
                "plans and pricing are listed on the Pricing page, and the sales team "
                "can quote anything custom."
            ),
            claims=knowledge_claims,
            sources=sources,
            actions=[("PRICING", "primary"), ("CONTACT_SALES", "secondary")],
            verifiability_status="qualified",
        )

    pricing_slug = "pricing-product-page"
    lines: list[dict] = []
    for entry, plan in rows:
        price = f"{entry.amount_minor_units / 100:.2f}"
        currency = entry.currency_code
        lines.append({
            "text": (
                f"{plan.name}: {currency} {price}/month, including up to {plan.max_numbers} "
                f"numbers, {plan.max_team_seats} team seats, and {plan.monthly_voice_minutes} "
                "voice minutes."
            ),
            "source_slugs": [pricing_slug],
        })

    addon = (
        db.query(AIReceptionistAddonRate)
        .filter(
            AIReceptionistAddonRate.status == CatalogEntryStatus.ACTIVE,
            AIReceptionistAddonRate.is_placeholder.is_(False),
        )
        .order_by(AIReceptionistAddonRate.created_at.desc())
        .first()
    )
    if addon is not None:
        lines.append({
            "text": (
                f"AI Receptionist add-on: {addon.currency_code} "
                f"{addon.monthly_price_minor_units / 100:.2f} per workspace/month "
                f"(includes {addon.included_minutes} minutes; "
                f"{addon.overage_rate_minor_units_per_minute / 100:.2f} per extra minute)."
            ),
            "source_slugs": [pricing_slug],
        })

    lines.append({
        "text": "Custom and enterprise commitments are quoted by sales - there's no self-serve price for them.",
        "source_slugs": [pricing_slug],
    })

    all_claims = lines + knowledge_claims
    text = (
        "Here's what the current approved price catalog shows (monthly, before number "
        "rental, usage, and taxes): "
        + " ".join(claim["text"] for claim in all_claims)
    )
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="PLANS_OVERVIEW",
        text=text,
        claims=all_claims,
        sources=sources,
        actions=[("PRICING", "primary"), ("CONTACT_SALES", "secondary")],
        suggestions=[{"label": "Getting started", "value": "ACTIVATION_GUIDE", "kind": "intent"}],
        verifiability_status="verified",
    )


def _activation_guide(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    claims, sources = _claims_from_rows(_knowledge_rows(db, "ACTIVATION_GUIDE"))
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="ACTIVATION_GUIDE",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("START_FREE", "primary"), ("GET_LOCAL_NUMBER", "secondary")],
        verifiability_status="qualified",
    )


def _contact_handoff(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    """WF-01 CONTACT_HANDOFF - a destination-only response (no claims to
    cite: it's a routing script, not a knowledge statement)."""
    return _build_response(
        db=db,
        response_type="guidance",
        intent_id="CONTACT_HANDOFF",
        text=(
            "I can connect you with a person. Sales can help with plans, pricing, and "
            "volume questions; support can help with anything on an existing account. "
            "Which would you like?"
        ),
        claims=[],
        sources=[],
        actions=[("CONTACT_SALES", "primary"), ("CONTACT_SUPPORT", "secondary")],
        verifiability_status="none",
    )


def _porting_guide(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    """WF-07: states are GUIDANCE_ONLY / REVIEW_REQUIRED / NOT_SUPPORTED
    / UNKNOWN - derived here from the market registry's porting flag,
    with UNKNOWN the default whenever no data backs a stronger state. No
    LOA collection and no full numbers in chat (the sensitive-input
    rules additionally block pasted card/credential patterns)."""
    claims, sources = _claims_from_rows(_knowledge_rows(db, "PORTING_GUIDE"))
    flow = session.flow_context or {}
    country_code = flow.get("country")
    if country_code:
        country = db.query(SupportedCountry).filter(SupportedCountry.code == country_code).first()
        if country is not None:
            state = (
                "REVIEW_REQUIRED - it may be possible, subject to carrier review"
                if country.porting_supported
                else "NOT_SUPPORTED from the current market registry"
            )
            claims.append({
                "text": f"Porting status for {country.name}: {state}.",
                "source_slugs": ["porting-product-page"],
            })
    else:
        claims.append({
            "text": "Porting availability depends on the country and carrier - I can check a specific country if you tell me which one.",
            "source_slugs": ["porting-product-page"],
        })
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="PORTING_GUIDE",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("PORTING", "primary"), ("CONTACT_SUPPORT", "secondary")],
        verifiability_status="qualified",
    )


def _emergency_info(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    """WF-13's informational branch (E2): qualified guidance + the
    emergency-calling page. Never claims Zoiko numbers support emergency
    calling anywhere."""
    claims, sources = _claims_from_rows(_knowledge_rows(db, "EMERGENCY_INFO"))
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="EMERGENCY_INFO",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("EMERGENCY_CALLING", "primary")],
        verifiability_status="qualified",
    )


def _privacy_info(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    """WF-10: explains the widget's own privacy controls (clear view /
    end session) - the buttons themselves live in the widget's privacy
    menu, this answer is the in-chat explanation."""
    claims, sources = _claims_from_rows(_knowledge_rows(db, "PRIVACY_INFO"))
    return _build_response(
        db=db,
        response_type="answer",
        intent_id="PRIVACY_INFO",
        text=" ".join(c["text"] for c in claims),
        claims=claims,
        sources=sources,
        actions=[("PRIVACY_POLICY", "primary")],
        verifiability_status="qualified",
    )


def _unknown(db: Session, session: AssistantSession) -> AssistantMessageResponse:
    """WF-01 unknown state - honest non-comprehension plus a route to a
    human, never a guessed answer."""
    return _build_response(
        db=db,
        response_type="unknown",
        intent_id=None,
        text=(
            "I'm not sure I understood that. I can help with local numbers, calling, "
            "the AI Receptionist, plans and pricing, porting, and getting started - "
            "or I can connect you with a person."
        ),
        claims=[],
        sources=[],
        actions=[("CONTACT_SALES", "secondary")],
        suggestions=_quick_start_suggestions(db),
        verifiability_status="none",
    )


_INTENT_HANDLERS = {
    "RECEPTIONIST_OVERVIEW": _receptionist_overview,
    "RECEPTIONIST_EXAMPLE": _receptionist_example,
    "CALLING_OVERVIEW": _calling_overview,
    "PLANS_OVERVIEW": _plans_overview,
    "ACTIVATION_GUIDE": _activation_guide,
    "CONTACT_HANDOFF": _contact_handoff,
    "PORTING_GUIDE": _porting_guide,
    "EMERGENCY_INFO": _emergency_info,
    "PRIVACY_INFO": _privacy_info,
}
