import json
import uuid

from app.core.config import settings
from app.core.deps import invalidate_cached_user
from app.numbering.identity.models import User, UserRole
from app.platform_assistant import content, tools
from app.platform_assistant.models import PlatformAssistantContent, PlatformAssistantEvent, PlatformAssistantSession


def _signup_and_login(client, email: str) -> str:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "Platform Assistant Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    assert signup.status_code in (200, 201), signup.text
    return client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]


def _headers(token: str, session: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    if session:
        headers.update(
            {
                "X-Platform-Assistant-Session": session["session_id"],
                "X-Platform-Assistant-Token": session["session_token"],
            }
        )
    return headers


def _enable(monkeypatch):
    monkeypatch.setattr(settings, "platform_assistant_enabled", True)


def test_bootstrap_requires_auth(client):
    response = client.get("/api/v1/platform-assistant/bootstrap")
    assert response.status_code == 401


def test_disabled_flag_blocks_stateful_routes(client, monkeypatch):
    token = _signup_and_login(client, f"pa-disabled-{uuid.uuid4()}@example.com")
    monkeypatch.setattr(settings, "platform_assistant_enabled", False)
    bootstrap = client.get("/api/v1/platform-assistant/bootstrap", headers=_headers(token))
    assert bootstrap.status_code == 200
    assert bootstrap.json()["enabled"] is False
    create = client.post("/api/v1/platform-assistant/sessions", json={}, headers=_headers(token))
    assert create.status_code == 503


def test_bootstrap_lists_all_twelve_modules_and_read_only_copy(client, monkeypatch):
    token = _signup_and_login(client, f"pa-bootstrap-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    response = client.get("/api/v1/platform-assistant/bootstrap", headers=_headers(token))
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["ai_disclosure"] == "I'm an automated Zoiko Local assistant. Read-only."
    assert "Changes happen in the secure workspace" in body["read_only_notice"]
    assert len(body["supported_modules"]) == 12


def test_message_returns_evidence_and_registered_navigation_without_storing_text(client, db_session, monkeypatch):
    token = _signup_and_login(client, f"pa-message-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    created = client.post("/api/v1/platform-assistant/sessions", json={"module": "numbers"}, headers=_headers(token))
    assert created.status_code == 201
    session = created.json()
    assert session["context"]["tenant_scope"] == "server-derived"

    secret_phrase = "numbers available with zip code 94105"
    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": secret_phrase},
        headers=_headers(token, session),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["read_only"] is True
    assert body["tenant_scope"] == "server-derived"
    assert body["content_blocks"]
    assert body["evidence"]
    assert all(item["masked"] is True for item in body["evidence"])
    assert all(action["href"].startswith("/dashboard") and action["read_only"] is True for action in body["available_actions"])

    events = db_session.query(PlatformAssistantEvent).filter(PlatformAssistantEvent.session_id == session["session_id"]).all()
    assert events
    assert secret_phrase not in json.dumps([event.metadata_json for event in events])


def test_mutation_request_is_blocked_and_points_to_native_screen(client, monkeypatch):
    token = _signup_and_login(client, f"pa-mutation-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={"module": "overview"}, headers=_headers(token)).json()
    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": "Please buy a new number for me"},
        headers=_headers(token, session),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer_type"] == "mutation_blocked"
    assert body["risk_tier"] == "R4"
    assert "can't make the change" in body["content_blocks"][0]["text"]
    assert body["available_actions"][0]["href"].startswith("/dashboard")


def test_entitlement_denial_uses_permission_fallback(client, monkeypatch):
    token = _signup_and_login(client, f"pa-denied-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={"module": "analytics"}, headers=_headers(token)).json()
    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": "Give me an operations overview"},
        headers=_headers(token, session),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer_type"] == "permission_denied"
    assert "current workspace permissions" in body["content_blocks"][0]["text"]


def test_context_change_invalidates_session(client, db_session, monkeypatch):
    email = f"pa-context-{uuid.uuid4()}@example.com"
    token = _signup_and_login(client, email)
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={"module": "overview"}, headers=_headers(token)).json()
    user = db_session.query(User).filter(User.email == email).first()
    user.role = UserRole.VIEWER
    db_session.commit()
    invalidate_cached_user(user.id)

    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": "What can you see?"},
        headers=_headers(token, session),
    )
    assert response.status_code == 409
    row = db_session.query(PlatformAssistantSession).filter(PlatformAssistantSession.id == session["session_id"]).first()
    assert row.invalidation_reason == "context_changed"
    assert row.is_active is False


def test_session_token_mismatch_is_rejected(client, monkeypatch):
    token = _signup_and_login(client, f"pa-token-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={}, headers=_headers(token)).json()
    headers = _headers(token, session)
    headers["X-Platform-Assistant-Token"] = "wrong-token"
    response = client.post("/api/v1/platform-assistant/messages", json={"text": "hello"}, headers=headers)
    assert response.status_code == 401


def test_platform_content_is_seeded_by_service(client, db_session, monkeypatch):
    token = _signup_and_login(client, f"pa-content-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    client.get("/api/v1/platform-assistant/bootstrap", headers=_headers(token))
    rows = db_session.query(PlatformAssistantContent).all()
    keys = {row.content_key for row in rows}
    assert {"ai_disclosure", "read_only_notice", "mutation_fallback", "unknown_fallback"}.issubset(keys)


class _DummyUser:
    account_id = "00000000-0000-0000-0000-000000000001"


def test_billing_answer_reads_usage_from_authoritative_resources(monkeypatch):
    monkeypatch.setattr(tools.billing_service, "get_entitlement_snapshot", lambda db, account_id: {"messaging.enabled": True})
    monkeypatch.setattr(
        tools.billing_service,
        "get_usage_summary",
        lambda db, account_id: {
            "plan_name": "Starter",
            "resources": [{"resource": "voice_minutes", "used": 12.5, "limit": 100}],
        },
    )
    monkeypatch.setattr(tools.billing_service, "list_account_billing_history", lambda db, account_id, limit: [])

    result = tools._billing(None, _DummyUser())
    items = [item for block in result.content_blocks for item in block.get("items", [])]
    assert "Plan: Starter." in items
    assert "voice_minutes: 12.5 of 100" in " ".join(items)


def test_support_answer_uses_authoritative_overall_health(monkeypatch):
    monkeypatch.setattr(tools.ops_service, "list_incidents", lambda db, limit: [])
    monkeypatch.setattr(tools.ops_service, "get_synthetic_check_summary", lambda db: {"overall_healthy": False, "checks": []})

    result = tools._support(None, _DummyUser())
    items = [item for block in result.content_blocks for item in block.get("items", [])]
    assert "Overall check status: attention needed" in items
    assert "No overall check status available" not in " ".join(items)


def test_platform_greeting_gets_a_real_answer(client, monkeypatch):
    token = _signup_and_login(client, f"pa-hi-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={}, headers=_headers(token)).json()

    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": "Hi"},
        headers=_headers(token, session),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["answer_type"] == "answer"
    assert "Hi!" in body["content_blocks"][0]["text"]


def test_platform_how_do_i_get_a_number_is_guidance_not_a_blocked_mutation(client, monkeypatch):
    token = _signup_and_login(client, f"pa-number-help-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={}, headers=_headers(token)).json()

    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": "How do I get a number?"},
        headers=_headers(token, session),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["answer_type"] == "answer"
    assert "Numbers area" in body["content_blocks"][0]["text"]
    assert any(action["destination_key"] == "DASHBOARD_NUMBERS" for action in body["available_actions"])


def _ask_platform_faq(client, monkeypatch, email: str, question: str) -> dict:
    token = _signup_and_login(client, f"{email}-{uuid.uuid4()}@example.com")
    _enable(monkeypatch)
    session = client.post("/api/v1/platform-assistant/sessions", json={}, headers=_headers(token)).json()
    response = client.post(
        "/api/v1/platform-assistant/messages",
        json={"text": question},
        headers=_headers(token, session),
    )
    assert response.status_code == 200
    return response.json()


def test_platform_calls_faq_points_to_real_screen(client, monkeypatch):
    body = _ask_platform_faq(client, monkeypatch, "pa-calls-faq", "Where are my call recaps?")
    assert body["answer_type"] == "answer"
    assert "Calls area" in body["content_blocks"][0]["text"]
    assert "DASHBOARD_CALLS" in {action["destination_key"] for action in body["available_actions"]}


def test_platform_porting_faq_points_to_real_screen(client, monkeypatch):
    body = _ask_platform_faq(client, monkeypatch, "pa-porting-faq", "Where do I track my port?")
    assert body["answer_type"] == "answer"
    assert "Numbers area" in body["content_blocks"][0]["text"]
    assert "DASHBOARD_NUMBERS" in {action["destination_key"] for action in body["available_actions"]}


def test_platform_video_faq_points_to_real_screen(client, monkeypatch):
    body = _ask_platform_faq(client, monkeypatch, "pa-video-faq", "Where can I see video meetings?")
    assert body["answer_type"] == "answer"
    assert "Video area" in body["content_blocks"][0]["text"]
    assert "DASHBOARD_VIDEO" in {action["destination_key"] for action in body["available_actions"]}


def test_platform_compliance_faq_points_to_real_screen(client, monkeypatch):
    body = _ask_platform_faq(client, monkeypatch, "pa-compliance-faq", "Where are retention policies?")
    assert body["answer_type"] == "answer"
    assert "Compliance area" in body["content_blocks"][0]["text"]
    assert "DASHBOARD_COMPLIANCE" in {action["destination_key"] for action in body["available_actions"]}


def test_general_faq_and_modules_reference_registered_evidence_and_destinations():
    source_ids = set(content.EVIDENCE_SOURCES)
    destination_keys = set(content.DESTINATIONS)

    for entry in content.GENERAL_FAQ:
        assert entry["evidence"], entry["key"]
        assert set(entry["evidence"]) <= source_ids
        assert set(entry["actions"]) <= destination_keys

    assert {module["destination_key"] for module in content.MODULES.values()} <= destination_keys
