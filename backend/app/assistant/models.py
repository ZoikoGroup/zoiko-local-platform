"""Zoiko Local public website assistant ("Zoiko Local Assistant") - the 13
chatbot engineering wireframes (WF-01 through WF-13, 24 October 2026). Five
tables, all following the repo's standing rules (UUID pk + created_at on
every table, rules as data - never hardcoded if-statements):

- AssistantIntent - the WF-01 "fixed, versioned intent IDs" registry: the
  six public quick-start intents plus the classifier-only ones (porting,
  emergency info, privacy, receptionist example). Keywords for free-text
  classification live here as data, so intent routing is tunable without a
  deploy - same discipline as ComplianceRule/GuardrailRule.
- AssistantKnowledgeSource - WF-11's source registry: every answer the
  assistant gives cites rows here. authority_class encodes the doc's A0-A4
  evidence hierarchy; reviewed_at/content_version are the freshness signals.
- AssistantSafetyRule - WF-13's urgent-path classifier AND WF-10's
  sensitive-input (secret redaction) detector, both stored as regex
  patterns in data rather than hardcoded Python constants, mirroring
  GuardrailRule's design exactly.
- AssistantDestination - the allowlist of approved outbound destinations
  (WF-01/WF-03: "allowlisted destination IDs only - never model-authored
  URLs"). Every CTA the assistant renders resolves to a row here.
- AssistantSession - anonymous, cookieless public sessions (WF-01/WF-10):
  a client-held opaque bearer token (hash stored, never the token itself),
  a short TTL, and a flow_context column holding ONLY validated non-sensitive
  enums (country/customer-type selections for WF-02's guided flow). No raw
  chat text is ever persisted anywhere in this module - transcripts live
  in the visitor's browser only, per WF-10's privacy-minimization posture.
"""

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.ids import new_uuid


class AssistantAuthorityClass(str, enum.Enum):
    """WF-11 (Evidence-Linked Answers) evidence hierarchy, highest to
    lowest authority. A0 is the only class allowed to state live facts
    (inventory, real-time eligibility); the assistant as built never
    holds A0 sources in chat - number availability is only ever claimed
    by the real purchase flow's signed provider response, never inferred
    here. A4 may never be cited at all: an unattributed claim is dropped,
    not shown."""

    A0 = "a0_realtime_provider"
    A1 = "a1_official_external"
    A2 = "a2_internal_policy"
    A3 = "a3_product_content"
    A4 = "a4_unattributed"


class AssistantSafetyCategory(str, enum.Enum):
    """WF-13 urgent-path rules and WF-10 secret-redaction rules share one
    table - both are deterministic regex classifiers over inbound free
    text, both must be data not code, and both outrank intent routing
    (see service.handle_message's fixed ordering)."""

    URGENT = "urgent"
    SENSITIVE = "sensitive"


class AssistantIntent(Base):
    """WF-01: "Six fixed, versioned intent IDs" exposed as quick-start
    chips, plus the doc's allowance for additional routed-only intents
    behind free-text classification (WF-02/07/10/13 informational
    paths). keywords drive the deterministic free-text classifier
    (service.classify_intent): intents are scanned in sort_order, first
    keyword hit wins - order is therefore part of the contract (porting
    phrases must be checked before generic "number" phrases, etc.)."""

    __tablename__ = "assistant_intents"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    intent_id: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    keywords: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_quick_start: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantKnowledgeSource(Base):
    """WF-11 source registry. Each row IS one claim: the assistant's answer
    for an intent is the joined body of every active row tagged with that
    intent_id, and the response's claims[]/sources[] arrays are derived
    from the same rows - so an answer can never contain text that isn't
    backed by a registered, versioned, dated source row (WF-11's core
    invariant: "no uncited claims"). Staff edits to a body take effect on
    the next uncached bootstrap; content_version/reviewed_at are surfaced
    to the client so the UI can show evidence cards."""

    __tablename__ = "assistant_knowledge_sources"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    publisher: Mapped[str] = mapped_column(String(120), nullable=False, default="Zoiko Local")
    canonical_url: Mapped[str] = mapped_column(String(500), nullable=False)
    authority_class: Mapped[AssistantAuthorityClass] = mapped_column(
        Enum(AssistantAuthorityClass, name="assistant_authority_class_enum"), nullable=False
    )
    intent_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_version: Mapped[str] = mapped_column(String(30), nullable=False)
    market: Mapped[str] = mapped_column(String(10), nullable=False, default="GLOBAL", server_default="GLOBAL")
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantSafetyRule(Base):
    """WF-13's urgent-path classifier and WF-10's sensitive-input detector
    as data. Patterns are Python regex source strings, compiled at read
    time (same approach as GuardrailRule.load_active_guardrail_patterns);
    an invalid regex row is skipped, never fatal - one bad pattern must
    not take the safety layer down. sort_order is only a tiebreaker for
    readability; any single match in a category fires."""

    __tablename__ = "assistant_safety_rules"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    category: Mapped[AssistantSafetyCategory] = mapped_column(
        Enum(AssistantSafetyCategory, name="assistant_safety_category_enum"), nullable=False, index=True
    )
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantDestination(Base):
    """WF-01/WF-03 destination allowlist. The assistant NEVER emits a URL
    that isn't an active row here - every CTA is resolved by
    destination_key through this table, so a model/template can't invent
    an outbound link. URLs are relative site paths owned by the marketing
    site repo (zoiko-local-react); rows are seeded to match that repo's
    real routes and only staff-approved changes should ever edit them."""

    __tablename__ = "assistant_destinations"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    destination_key: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssistantSession(Base):
    """WF-01/WF-10 anonymous session. No cookie, no account linkage, no
    PII: the client keeps the opaque session_token (secrets.token_urlsafe,
    only its SHA-256 hash is stored) and sends it as a request header, so
    there is no ambient browser authority to forge or leak. flow_context
    stores only server-validated enums (ISO country code from
    SupportedCountry, 'business'/'personal') for WF-02's guided flow -
    never free text. Sessions expire on a short TTL and are hard-deleted
    by the daily purge sweep (ops/scheduled_reconciliation), keeping
    storage bounded without a retention-policy layer of their own."""

    __tablename__ = "assistant_sessions"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=new_uuid)
    session_token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    page_context: Mapped[str | None] = mapped_column(String(200), nullable=True)
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="en-US")
    flow_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
