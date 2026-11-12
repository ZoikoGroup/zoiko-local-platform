"""Real acceptance tests for the public website assistant (the WF-01..
WF-13 chatbot wireframes, v1.0, 24 October 2026) - each test pins a
behavior the wireframes themselves state as a requirement, not just an
implementation detail:

- WF-01: fixed quick-start chips, versioned content, unknown-state
  honesty, destination allowlist (never a model-authored URL).
- WF-02: country chips come from the market registry's public statuses,
  never from provider reach; inventory is never claimed.
- WF-06: plan prices come only from the approved ACTIVE catalog; no
  catalog -> honest degradation, never a guessed number.
- WF-10: sensitive input is discarded unprocessed and unstored; only a
  metadata audit event remains.
- WF-11: every claim carries citations.
- WF-12: unsupported locales fail closed to en-US.
- WF-13: the urgent path outranks intent classification and returns the
  static script verbatim.

Content is seeded from app.assistant.content via the ORM (the test schema
is built with create_all, not migrations), so tests exercise exactly
what the seed migration inserts.
"""

from datetime import timedelta

from app.assistant import content, service
from app.assistant.models import (
    AssistantAuthorityClass,
    AssistantDestination,
    AssistantIntent,
    AssistantKnowledgeSource,
    AssistantSafetyCategory,
    AssistantSafetyRule,
    AssistantSession,
)
from app.audit.models import AuditEvent
from app.billing.models import (
    AIReceptionistAddonRate,
    BillingPeriod,
    CatalogEntryStatus,
    Plan,
    PriceCatalogEntry,
)
from app.compliance.models import ComplianceRule
from app.core.config import settings
from app.numbering.numbers.models import (
    MarketActivationStatus,
    NumberEligibilityRule,
    SupportedCountry,
)


def _seed_assistant_content(db):
    """Idempotent per natural key (like the seed migration) - safe to call
    twice in one test, and safe if the shared dev database already carries
    these rows from a migration run."""
    for intent in content.INTENTS:
        if db.query(AssistantIntent).filter(AssistantIntent.intent_id == intent["intent_id"]).first():
            continue
        db.add(AssistantIntent(
            intent_id=intent["intent_id"],
            title=intent["title"],
            description=intent["description"],
            keywords=intent.get("keywords") or [],
            sort_order=intent["sort_order"],
            is_quick_start=intent["is_quick_start"],
            is_active=True,
        ))
    for source in content.KNOWLEDGE_SOURCES:
        if db.query(AssistantKnowledgeSource).filter(AssistantKnowledgeSource.slug == source["slug"]).first():
            continue
        db.add(AssistantKnowledgeSource(
            slug=source["slug"],
            title=source["title"],
            publisher="Zoiko Local",
            canonical_url=source["canonical_url"],
            authority_class=AssistantAuthorityClass(source["authority_class"]),
            intent_id=source["intent_id"],
            body=source["body"],
            content_version=content.CONTENT_VERSION,
            is_active=True,
        ))
    for rule in content.SAFETY_RULES:
        if db.query(AssistantSafetyRule).filter(AssistantSafetyRule.pattern == rule["pattern"]).first():
            continue
        db.add(AssistantSafetyRule(
            category=AssistantSafetyCategory(rule["category"]),
            pattern=rule["pattern"],
            sort_order=rule["sort_order"],
            is_active=True,
        ))
    for dest in content.DESTINATIONS:
        if db.query(AssistantDestination).filter(AssistantDestination.destination_key == dest["destination_key"]).first():
            continue
        db.add(AssistantDestination(
            destination_key=dest["destination_key"],
            label=dest["label"],
            url=dest["url"],
            is_active=True,
        ))
    db.commit()


def _create_test_countries(db):
    """Dedicated countries (never the real seeded 8, which conftest's
    _test_markets_stay_paid_open fixture keeps flipping to OPEN for every
    other test in the suite): one public market, one not-yet-public
    market, one public-but-porting-unsupported market."""
    open_market = SupportedCountry(
        code="T1", name="Testland One", market_status=MarketActivationStatus.OPEN,
        porting_supported=True, customer_signup_enabled=True, number_search_enabled=True,
        number_purchase_enabled=True,
    )
    not_public = SupportedCountry(
        code="T2", name="Testland Two", market_status=MarketActivationStatus.LAUNCHING,
    )
    restricted = SupportedCountry(
        code="T3", name="Testland Three", market_status=MarketActivationStatus.RESTRICTED,
        porting_supported=False,
    )
    db.add_all([open_market, not_public, restricted])
    db.add(NumberEligibilityRule(
        country="T1", number_type="local", required_evidence=["proof_of_address"], is_active=True,
    ))
    db.add(ComplianceRule(
        country="T1", requirement_type="identity_verification",
        required_documents=["government_id"], is_active=True,
    ))
    db.commit()


def _start_session(client, db):
    _seed_assistant_content(db)
    response = client.post("/api/v1/assistant/sessions", json={})
    assert response.status_code == 201
    return response.json()


def _send(client, session, payload):
    return client.post(
        "/api/v1/assistant/messages",
        json=payload,
        headers={
            "X-Assistant-Session": session["session_id"],
            "X-Assistant-Token": session["session_token"],
        },
    )


def _deactivate_catalog(db):
    """The real dev DB carries genuinely ACTIVE catalog rows (approved
    2026-08-20) - retire them inside this test's own transaction (rolled
    back at teardown, same as every other test-time DB change) so plan
    tests can assert exact seeded prices instead of whatever the shared
    database happens to contain."""
    db.query(PriceCatalogEntry).filter(
        PriceCatalogEntry.status == CatalogEntryStatus.ACTIVE,
        PriceCatalogEntry.billing_period == BillingPeriod.MONTHLY,
        PriceCatalogEntry.market == "GLOBAL",
    ).update({"status": CatalogEntryStatus.RETIRED}, synchronize_session=False)
    db.query(AIReceptionistAddonRate).filter(
        AIReceptionistAddonRate.status == CatalogEntryStatus.ACTIVE,
    ).update({"status": CatalogEntryStatus.RETIRED}, synchronize_session=False)
    db.commit()


# ---------------------------------------------------------------------------
# WF-01 bootstrap / identity
# ---------------------------------------------------------------------------

def test_bootstrap_lists_quick_start_intents_and_identity_strings(client, db_session):
    _seed_assistant_content(db_session)

    response = client.get("/api/v1/assistant/bootstrap")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["assistant_name"] == "Zoiko Local Assistant"
    assert "Locu" not in body["assistant_name"]
    assert body["safety_notice"]
    assert body["ai_disclosure"]
    assert body["content_version"] == content.CONTENT_VERSION
    assert body["default_locale"] == "en-US"
    assert [a["intent_id"] for a in body["actions"]] == [
        "NUMBER_DISCOVERY", "RECEPTIONIST_OVERVIEW", "CALLING_OVERVIEW",
        "PLANS_OVERVIEW", "ACTIVATION_GUIDE", "CONTACT_HANDOFF",
    ]


def test_bootstrap_kill_switch_flips_immediately_despite_cache(client, db_session, monkeypatch):
    """WF-01 kill switch: enabled=false must reach the widget on the very
    next bootstrap even while the cached registry payload is still warm,
    and the stateful endpoints must refuse outright."""
    _seed_assistant_content(db_session)

    first = client.get("/api/v1/assistant/bootstrap").json()
    assert first["enabled"] is True

    monkeypatch.setattr(settings, "public_assistant_enabled", False)
    second = client.get("/api/v1/assistant/bootstrap").json()
    assert second["enabled"] is False
    assert client.post("/api/v1/assistant/sessions", json={}).status_code == 503
    assert client.post(
        "/api/v1/assistant/messages", json={},
        headers={"X-Assistant-Session": "x", "X-Assistant-Token": "y"},
    ).status_code == 503


# ---------------------------------------------------------------------------
# WF-01/WF-10 session lifecycle
# ---------------------------------------------------------------------------

def test_create_session_returns_token_once_and_fails_locale_closed(client, db_session):
    _seed_assistant_content(db_session)

    created = client.post("/api/v1/assistant/sessions", json={"locale": "fr-FR"})
    assert created.status_code == 201
    body = created.json()
    # WF-12: only reviewed locales resolve; anything else fails closed.
    assert body["locale_used"] == "en-US"
    assert body["locale_fallback"] is True
    assert body["session_token"]
    # The token is shown exactly once and only its hash is stored.
    row = db_session.query(AssistantSession).filter(AssistantSession.id == body["session_id"]).one()
    assert row.session_token_hash != body["session_token"]
    assert db_session.query(AuditEvent).filter(
        AuditEvent.action == "assistant.session_created",
        AuditEvent.target == f"assistant_session:{body['session_id']}",
    ).count() == 1

    supported = client.post("/api/v1/assistant/sessions", json={"locale": "en-US"}).json()
    assert supported["locale_fallback"] is False


def test_messages_require_valid_session_credentials(client, db_session):
    _seed_assistant_content(db_session)

    assert client.post("/api/v1/assistant/messages", json={"text": "hello"}).status_code == 422

    session = _start_session(client, db_session)
    wrong = client.post(
        "/api/v1/assistant/messages",
        json={"text": "hello"},
        headers={"X-Assistant-Session": session["session_id"], "X-Assistant-Token": "not-the-token"},
    )
    assert wrong.status_code == 401


def test_expired_session_is_rejected_with_401(client, db_session):
    session = _start_session(client, db_session)
    db_session.query(AssistantSession).filter(
        AssistantSession.id == session["session_id"]
    ).update({"expires_at": service._now() - timedelta(minutes=1)})
    db_session.commit()

    response = _send(client, session, {"text": "hello"})
    assert response.status_code == 401


def test_end_session_is_idempotent_and_blocks_further_messages(client, db_session):
    session = _start_session(client, db_session)
    headers = {
        "X-Assistant-Session": session["session_id"],
        "X-Assistant-Token": session["session_token"],
    }

    first = client.post("/api/v1/assistant/sessions/end", headers=headers)
    assert first.status_code == 200
    assert first.json()["ended"] is True

    again = client.post("/api/v1/assistant/sessions/end", headers=headers)
    assert again.status_code == 200
    assert again.json()["ended"] is True

    assert _send(client, session, {"text": "hello"}).status_code == 401


def test_purge_deletes_only_sessions_past_the_grace_window(db_session):
    _seed_assistant_content(db_session)

    beyond_grace = service.create_session(db_session)
    just_expired = service.create_session(db_session)
    ended_long_ago = service.create_session(db_session)
    db_session.query(AssistantSession).filter(
        AssistantSession.id == beyond_grace["session_id"]
    ).update({"expires_at": service._now() - timedelta(hours=25)})
    db_session.query(AssistantSession).filter(
        AssistantSession.id == just_expired["session_id"]
    ).update({"expires_at": service._now() - timedelta(hours=1)})
    db_session.query(AssistantSession).filter(
        AssistantSession.id == ended_long_ago["session_id"]
    ).update({
        "ended_at": service._now() - timedelta(hours=25),
        "expires_at": service._now() - timedelta(hours=26),
    })
    db_session.commit()

    result = service.purge_expired_sessions(db_session)
    assert result["deleted"] == 2
    remaining = {s.id for s in db_session.query(AssistantSession).all()}
    assert just_expired["session_id"] in remaining
    assert beyond_grace["session_id"] not in remaining
    assert ended_long_ago["session_id"] not in remaining


# ---------------------------------------------------------------------------
# WF-13 urgent path / WF-10 sensitive input
# ---------------------------------------------------------------------------

def test_urgent_path_outranks_intent_classification_and_is_verbatim(client, db_session):
    session = _start_session(client, db_session)

    # "plans" would classify as PLANS_OVERVIEW; "heart attack" must win.
    response = _send(client, session, {"text": "I think I'm having a heart attack, what plans do you offer?"})
    assert response.status_code == 200
    body = response.json()
    assert body["response_type"] == "safety_urgent"
    assert body["text"] == content.URGENT_RESPONSE_TEXT
    assert body["verifiability_status"] == "none"
    assert [(a["destination_key"], a["url"]) for a in body["actions"]] == [
        ("EMERGENCY_CALLING", "/emergency-calling"),
    ]
    assert db_session.query(AuditEvent).filter(
        AuditEvent.action == "assistant.urgent_path_triggered"
    ).count() == 1


def test_urgent_action_dropped_not_substituted_when_destination_inactive(client, db_session):
    """WF-01: a CTA whose allowlist row is inactive disappears entirely -
    there is never a fallback or invented URL."""
    session = _start_session(client, db_session)
    db_session.query(AssistantDestination).filter(
        AssistantDestination.destination_key == "EMERGENCY_CALLING"
    ).update({"is_active": False})
    db_session.commit()

    body = _send(client, session, {"text": "I am having an emergency, help"}).json()
    assert body["response_type"] == "safety_urgent"
    assert body["actions"] == []


def test_sensitive_input_is_blocked_and_never_stored(client, db_session):
    session = _start_session(client, db_session)

    response = _send(client, session, {"text": "my card number is 4111 1111 1111 1111"})
    assert response.status_code == 200
    body = response.json()
    assert body["response_type"] == "sensitive_blocked"
    assert body["text"] == content.SENSITIVE_RESPONSE_TEXT
    assert body["claims"] == []
    assert body["sources"] == []

    # Nothing text-shaped persisted anywhere: the session row carries
    # only counters/timestamps, and the audit event only counts.
    row = db_session.query(AssistantSession).filter(AssistantSession.id == session["session_id"]).one()
    assert "4111" not in str(row.flow_context)
    assert "4111" not in str(row.page_context)
    event = db_session.query(AuditEvent).filter(
        AuditEvent.action == "assistant.sensitive_input_blocked"
    ).one()
    assert "4111" not in str(event.after_hash)


# ---------------------------------------------------------------------------
# WF-02 number discovery
# ---------------------------------------------------------------------------

def test_number_discovery_full_flow_is_grounded_and_honest(client, db_session):
    _seed_assistant_content(db_session)
    _create_test_countries(db_session)
    session = _start_session(client, db_session)

    step1 = _send(client, session, {"intent_id": "NUMBER_DISCOVERY"}).json()
    assert step1["flow_step"] == "country"
    values = {s["value"] for s in step1["suggestions"]}
    assert "T1" in values          # OPEN market is offered
    assert "T2" not in values       # LAUNCHING market is never offered

    step2 = _send(client, session, {
        "intent_id": "NUMBER_DISCOVERY", "selection": {"country": "T1"},
    }).json()
    assert step2["flow_step"] == "customer_type"

    step3 = _send(client, session, {
        "intent_id": "NUMBER_DISCOVERY", "selection": {"customer_type": "business"},
    }).json()
    assert step3["flow_step"] == "summary"
    assert step3["response_type"] == "answer"
    assert "Testland One" in step3["text"]
    # Registry-grounded specifics, not guesses.
    assert "proof_of_address" in step3["text"]
    assert "identity_verification" in step3["text"]
    # WF-02's inventory rule: availability is never claimed from chat.
    assert "can't confirm live number availability" in step3["text"]
    # WF-11: every claim is cited, every citation shown.
    assert step3["claims"]
    assert all(c["source_slugs"] for c in step3["claims"])
    source_slugs = {s["slug"] for s in step3["sources"]}
    assert "global-coverage-page" in source_slugs
    assert [(a["destination_key"], a["style"]) for a in step3["actions"]][0] == ("GET_LOCAL_NUMBER", "primary")
    # Only validated enums ever entered flow_context.
    row = db_session.query(AssistantSession).filter(AssistantSession.id == session["session_id"]).one()
    assert row.flow_context == {"country": "T1", "customer_type": "business"}


def test_number_discovery_unknown_country_is_answered_honestly(client, db_session):
    _seed_assistant_content(db_session)
    _create_test_countries(db_session)
    session = _start_session(client, db_session)

    body = _send(client, session, {
        "intent_id": "NUMBER_DISCOVERY", "selection": {"country": "XX"},
    }).json()
    assert "I don't have XX" in body["text"]
    assert any(a["destination_key"] == "GLOBAL_COVERAGE" for a in body["actions"])


# ---------------------------------------------------------------------------
# WF-06 plans
# ---------------------------------------------------------------------------

def test_plans_overview_reads_only_the_approved_catalog(client, db_session):
    _seed_assistant_content(db_session)
    _deactivate_catalog(db_session)
    db_session.add(Plan(
        plan_code="chatbot_test_plan", name="Chatbot Test Plan", max_numbers=3,
        max_team_seats=2, monthly_voice_minutes=100, monthly_video_minutes=50,
        monthly_ai_summaries=10, sort_order=1,
    ))
    db_session.add(PriceCatalogEntry(
        plan_code="chatbot_test_plan", catalog_version="chatbot-test-v1",
        billing_period=BillingPeriod.MONTHLY, amount_minor_units=1299,
        currency_code="USD", status=CatalogEntryStatus.ACTIVE, is_placeholder=False,
        market="GLOBAL",
    ))
    db_session.add(AIReceptionistAddonRate(
        catalog_version="chatbot-addon-test-v1", monthly_price_minor_units=2900,
        included_minutes=100, overage_rate_minor_units_per_minute=39,
        currency_code="USD", status=CatalogEntryStatus.ACTIVE, is_placeholder=False,
    ))
    db_session.commit()
    session = _start_session(client, db_session)

    body = _send(client, session, {"intent_id": "PLANS_OVERVIEW"}).json()
    assert body["verifiability_status"] == "verified"
    assert "Chatbot Test Plan: USD 12.99/month, including up to 3 numbers, 2 team seats, and 100 voice minutes." in body["text"]
    assert "AI Receptionist add-on: USD 29.00 per workspace/month (includes 100 minutes; 0.39 per extra minute)." in body["text"]
    # Totals are always deferred to checkout, never quoted here (WF-06).
    assert "checkout" in body["text"].lower()
    assert all(c["source_slugs"] == ["pricing-product-page"] for c in body["claims"] if "Chatbot Test Plan" in c["text"])


def test_plans_overview_degrades_honestly_with_no_visible_catalog(client, db_session):
    _seed_assistant_content(db_session)
    _deactivate_catalog(db_session)
    session = _start_session(client, db_session)

    body = _send(client, session, {"intent_id": "PLANS_OVERVIEW"}).json()
    assert "can't see approved plan prices" in body["text"]
    assert body["verifiability_status"] == "qualified"
    assert any(a["destination_key"] == "PRICING" for a in body["actions"])


# ---------------------------------------------------------------------------
# WF-07 porting, WF-01 unknown / handoff
# ---------------------------------------------------------------------------

def test_porting_states_derive_from_the_market_registry(client, db_session):
    _seed_assistant_content(db_session)
    _create_test_countries(db_session)

    unscoped = _start_session(client, db_session)
    no_country = _send(client, unscoped, {"text": "can I port my number over?"}).json()
    assert no_country["intent_id"] == "PORTING_GUIDE"
    assert "depends on the country" in no_country["text"]

    session = _start_session(client, db_session)
    _send(client, session, {"intent_id": "NUMBER_DISCOVERY", "selection": {"country": "T1"}})

    supported = _send(client, session, {"text": "keep my number when I switch?"}).json()
    assert supported["intent_id"] == "PORTING_GUIDE"
    assert "REVIEW_REQUIRED" in supported["text"]

    other = _start_session(client, db_session)
    _send(client, other, {"intent_id": "NUMBER_DISCOVERY", "selection": {"country": "T3"}})
    unsupported = _send(client, other, {"text": "port my number please"}).json()
    assert "NOT_SUPPORTED" in unsupported["text"]


def test_classification_precedence_is_sort_order_not_declaration_order(client, db_session):
    session = _start_session(client, db_session)

    # "port my number" contains "number" (NUMBER_DISCOVERY) but PORTING_GUIDE
    # (sort_order 5) is checked first.
    porting = _send(client, session, {"text": "how do I port my number to you"}).json()
    assert porting["intent_id"] == "PORTING_GUIDE"

    # An informational emergency question is answered, not escalated to the
    # urgent path (WF-13's E2 information route).
    info = _send(client, session, {"text": "does zoiko support 911 calling"}).json()
    assert info["intent_id"] == "EMERGENCY_INFO"
    assert info["response_type"] == "answer"


def test_unknown_input_admits_non_comprehension(client, db_session):
    session = _start_session(client, db_session)

    gibberish = _send(client, session, {"text": "flurble wobble zorp"}).json()
    assert gibberish["response_type"] == "unknown"
    assert gibberish["intent_id"] is None
    assert gibberish["verifiability_status"] == "none"
    assert {s["value"] for s in gibberish["suggestions"]} >= {"NUMBER_DISCOVERY", "PLANS_OVERVIEW"}

    stale_chip = _send(client, session, {"intent_id": "NOT_A_REAL_INTENT"}).json()
    assert stale_chip["response_type"] == "unknown"


def test_contact_handoff_offers_both_teams(client, db_session):
    session = _start_session(client, db_session)

    body = _send(client, session, {"intent_id": "CONTACT_HANDOFF"}).json()
    assert {a["destination_key"] for a in body["actions"]} == {"CONTACT_SALES", "CONTACT_SUPPORT"}
    assert body["verifiability_status"] == "none"
    assert body["claims"] == []
