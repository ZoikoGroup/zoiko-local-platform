from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _create_and_login_staff(db_session, client, email: str, role=PlatformStaffRole.SUPPORT) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def test_provider_status_requires_staff_auth(client):
    response = client.get("/ops/provider-status")
    assert response.status_code == 401


def test_provider_status_customer_token_rejected(client):
    client.post(
        "/auth/signup",
        json={
            "account_name": "Ops Test Co",
            "account_type": "business",
            "email": "opscustomer@example.com",
            "password": "supersecret123",
        },
    )
    login = client.post(
        "/auth/login", json={"email": "opscustomer@example.com", "password": "supersecret123"}
    )
    response = client.get(
        "/ops/provider-status", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert response.status_code == 401


def test_provider_status_reports_not_configured_when_no_credentials(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.telecom.twilio.settings.twilio_account_sid", "")
    monkeypatch.setattr("app.integrations.llm.groq.settings.groq_api_key", "")
    monkeypatch.setattr("app.integrations.kyc.stripe_identity.settings.stripe_secret_key", "")
    monkeypatch.setattr("app.integrations.billing.stripe_checkout.settings.stripe_payments_secret_key", "")
    monkeypatch.setattr("app.integrations.notifications.email.settings.resend_api_key", "")
    monkeypatch.setattr("app.integrations.storage.s3.settings.s3_bucket", "")
    monkeypatch.setattr("app.integrations.video.livekit.settings.livekit_url", "")
    monkeypatch.setattr("app.integrations.embeddings.cohere.settings.cohere_api_key", "")

    token = _create_and_login_staff(db_session, client, "opssupport1@zoikolocal.com")
    response = client.get("/ops/provider-status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    providers = {p["name"]: p for p in response.json()["providers"]}
    assert set(providers.keys()) == {
        "twilio", "livekit", "groq", "stripe_identity", "stripe_payments", "resend", "storage_s3", "cohere",
    }
    for provider in providers.values():
        assert provider["configured"] is False
        assert provider["ok"] is False


def test_provider_status_reports_ok_when_health_checks_succeed(client, db_session, monkeypatch):
    async def _async_ok():
        return {"configured": True, "ok": True, "detail": None}

    monkeypatch.setattr("app.integrations.telecom.twilio.health_check", lambda: {"configured": True, "ok": True, "detail": None})
    monkeypatch.setattr("app.integrations.llm.groq.health_check", lambda: {"configured": True, "ok": True, "detail": None})
    monkeypatch.setattr("app.integrations.kyc.stripe_identity.health_check", lambda: {"configured": True, "ok": True, "detail": None})
    monkeypatch.setattr("app.integrations.billing.stripe_checkout.health_check", lambda: {"configured": True, "ok": True, "detail": None})
    monkeypatch.setattr("app.integrations.notifications.email.health_check", lambda: {"configured": True, "ok": True, "detail": None})
    monkeypatch.setattr("app.integrations.storage.s3.health_check", lambda: {"configured": True, "ok": True, "detail": None})
    monkeypatch.setattr("app.integrations.video.livekit.health_check", _async_ok)
    monkeypatch.setattr("app.integrations.embeddings.cohere.health_check", lambda: {"configured": True, "ok": True, "detail": None})

    token = _create_and_login_staff(db_session, client, "opssupport2@zoikolocal.com")
    response = client.get("/ops/provider-status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    providers = response.json()["providers"]
    assert len(providers) == 8
    assert all(p["ok"] is True for p in providers)


def test_provider_status_reports_error_detail_when_configured_but_unreachable(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.integrations.telecom.twilio.health_check",
        lambda: {"configured": True, "ok": False, "detail": "simulated auth failure"},
    )

    token = _create_and_login_staff(db_session, client, "opssupport3@zoikolocal.com")
    response = client.get("/ops/provider-status", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    twilio_status = next(p for p in response.json()["providers"] if p["name"] == "twilio")
    assert twilio_status["configured"] is True
    assert twilio_status["ok"] is False
    assert twilio_status["detail"] == "simulated auth failure"


def test_public_status_requires_no_auth(client):
    from app.ops import service

    service._public_status_cache = None
    response = client.get("/ops/status")
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] in {"operational", "degraded"}
    assert len(body["components"]) == 8


def test_public_status_never_leaks_provider_names_or_error_detail(client, monkeypatch):
    from app.ops import service

    service._public_status_cache = None
    monkeypatch.setattr(
        "app.integrations.telecom.twilio.health_check",
        lambda: {"configured": True, "ok": False, "detail": "secret internal auth failure detail"},
    )

    response = client.get("/ops/status")
    assert response.status_code == 200
    body = response.json()

    assert "secret internal auth failure detail" not in response.text
    assert "twilio" not in response.text
    names = {c["name"] for c in body["components"]}
    assert names == {
        "Calling & SMS",
        "Video",
        "AI Receptionist & Call Summaries",
        "Identity Verification",
        "Number Purchase Payments",
        "Email Notifications",
        "Recording Storage",
        "Semantic Search",
    }
    assert body["overall"] == "degraded"
    calling = next(c for c in body["components"] if c["name"] == "Calling & SMS")
    assert calling["status"] == "degraded"


def test_public_status_is_cached_between_requests(client, monkeypatch):
    from app.ops import service

    service._public_status_cache = None
    call_count = 0

    def _counting_health_check():
        nonlocal call_count
        call_count += 1
        return {"configured": True, "ok": True, "detail": None}

    monkeypatch.setattr("app.integrations.telecom.twilio.health_check", _counting_health_check)

    client.get("/ops/status")
    client.get("/ops/status")

    assert call_count == 1


# --- Synthetic call monitoring ---


def test_run_synthetic_checks_requires_staff_auth(client):
    response = client.post("/ops/synthetic-checks/run")
    assert response.status_code == 401


def test_run_synthetic_checks_records_database_and_signature_checks(client, db_session, monkeypatch):
    monkeypatch.setattr("app.ops.service.settings.twilio_auth_token", "test_auth_token_123")
    monkeypatch.setattr("app.integrations.telecom.twilio.settings.twilio_auth_token", "test_auth_token_123")

    token = _create_and_login_staff(db_session, client, "opssynth1@zoikolocal.com")
    response = client.post("/ops/synthetic-checks/run", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    checks = {c["check_name"]: c for c in response.json()}
    assert checks["database_connectivity"]["success"] is True
    assert checks["twilio_webhook_signature_pipeline"]["success"] is True


def test_synthetic_signature_check_fails_when_twilio_auth_token_unconfigured(client, db_session, monkeypatch):
    monkeypatch.setattr("app.ops.service.settings.twilio_auth_token", "")

    token = _create_and_login_staff(db_session, client, "opssynth2@zoikolocal.com")
    response = client.post("/ops/synthetic-checks/run", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    checks = {c["check_name"]: c for c in response.json()}
    assert checks["twilio_webhook_signature_pipeline"]["success"] is False
    assert "not configured" in checks["twilio_webhook_signature_pipeline"]["detail"]


def test_synthetic_checks_skip_unconfigured_providers(client, db_session, monkeypatch):
    monkeypatch.setattr("app.integrations.telecom.twilio.settings.twilio_account_sid", "")
    monkeypatch.setattr("app.integrations.llm.groq.settings.groq_api_key", "")
    monkeypatch.setattr("app.integrations.kyc.stripe_identity.settings.stripe_secret_key", "")
    monkeypatch.setattr("app.integrations.billing.stripe_checkout.settings.stripe_payments_secret_key", "")
    monkeypatch.setattr("app.integrations.notifications.email.settings.resend_api_key", "")
    monkeypatch.setattr("app.integrations.storage.s3.settings.s3_bucket", "")
    monkeypatch.setattr("app.integrations.video.livekit.settings.livekit_url", "")
    monkeypatch.setattr("app.integrations.embeddings.cohere.settings.cohere_api_key", "")

    token = _create_and_login_staff(db_session, client, "opssynth3@zoikolocal.com")
    response = client.post("/ops/synthetic-checks/run", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    check_names = {c["check_name"] for c in response.json()}
    assert not any(name.startswith("provider_reachability_") for name in check_names)


def test_synthetic_checks_records_a_configured_and_reachable_provider(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.integrations.telecom.twilio.health_check", lambda: {"configured": True, "ok": True, "detail": None}
    )

    token = _create_and_login_staff(db_session, client, "opssynth4@zoikolocal.com")
    response = client.post("/ops/synthetic-checks/run", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    checks = {c["check_name"]: c for c in response.json()}
    assert checks["provider_reachability_twilio"]["success"] is True


def test_list_synthetic_checks_returns_persisted_history(client, db_session):
    token = _create_and_login_staff(db_session, client, "opssynth5@zoikolocal.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/ops/synthetic-checks/run", headers=headers)
    client.post("/ops/synthetic-checks/run", headers=headers)

    response = client.get("/ops/synthetic-checks", headers=headers)
    assert response.status_code == 200
    assert len(response.json()) >= 2

    filtered = client.get(
        "/ops/synthetic-checks", params={"check_name": "database_connectivity"}, headers=headers
    ).json()
    assert len(filtered) == 2
    assert all(c["check_name"] == "database_connectivity" for c in filtered)


def test_synthetic_checks_summary_reports_overall_health(client, db_session, monkeypatch):
    monkeypatch.setattr("app.ops.service.settings.twilio_auth_token", "test_auth_token_123")
    monkeypatch.setattr("app.integrations.telecom.twilio.settings.twilio_auth_token", "test_auth_token_123")
    # Blank every provider so only the two deterministic checks below get
    # recorded - the real .env in this environment may have real provider
    # credentials configured, which would make this assertion environment-
    # dependent otherwise.
    monkeypatch.setattr("app.integrations.telecom.twilio.settings.twilio_account_sid", "")
    monkeypatch.setattr("app.integrations.llm.groq.settings.groq_api_key", "")
    monkeypatch.setattr("app.integrations.kyc.stripe_identity.settings.stripe_secret_key", "")
    monkeypatch.setattr("app.integrations.billing.stripe_checkout.settings.stripe_payments_secret_key", "")
    monkeypatch.setattr("app.integrations.notifications.email.settings.resend_api_key", "")
    monkeypatch.setattr("app.integrations.storage.s3.settings.s3_bucket", "")
    monkeypatch.setattr("app.integrations.video.livekit.settings.livekit_url", "")
    monkeypatch.setattr("app.integrations.embeddings.cohere.settings.cohere_api_key", "")

    token = _create_and_login_staff(db_session, client, "opssynth6@zoikolocal.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/ops/synthetic-checks/run", headers=headers)

    response = client.get("/ops/synthetic-checks/summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["overall_healthy"] is True
    assert {c["check_name"] for c in body["checks"]} == {
        "database_connectivity", "twilio_webhook_signature_pipeline",
    }


def test_synthetic_checks_summary_is_unhealthy_when_any_check_fails(client, db_session, monkeypatch):
    monkeypatch.setattr("app.ops.service.settings.twilio_auth_token", "")

    token = _create_and_login_staff(db_session, client, "opssynth7@zoikolocal.com")
    headers = {"Authorization": f"Bearer {token}"}
    client.post("/ops/synthetic-checks/run", headers=headers)

    response = client.get("/ops/synthetic-checks/summary", headers=headers)
    assert response.json()["overall_healthy"] is False
