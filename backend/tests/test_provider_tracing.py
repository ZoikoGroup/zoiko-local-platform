from app.observability.models import ProviderCallTrace
from app.observability.service import (
    current_request_id,
    provider_call_latency_summary,
    trace_provider_call,
)
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole


def _create_and_login_staff(db_session, client, email: str, role=PlatformStaffRole.SUPPORT) -> str:
    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    response = client.post("/staff/login", json={"email": email, "password": "staffpass123"})
    return response.json()["access_token"]


def test_trace_provider_call_records_success(db_session):
    token = current_request_id.set("req-test-success")
    try:
        with trace_provider_call("fake_provider", "fake_op"):
            pass
    finally:
        current_request_id.reset(token)

    row = (
        db_session.query(ProviderCallTrace)
        .filter(ProviderCallTrace.provider == "fake_provider", ProviderCallTrace.operation == "fake_op")
        .first()
    )
    assert row is not None
    assert row.request_id == "req-test-success"
    assert row.success is True
    assert row.error_detail is None
    assert row.duration_ms >= 0


def test_trace_provider_call_records_failure_and_does_not_swallow_the_exception(db_session):
    token = current_request_id.set("req-test-failure")
    try:
        try:
            with trace_provider_call("fake_provider", "fake_op_fails"):
                raise ValueError("simulated provider failure")
            assert False, "the exception must propagate, not be swallowed"
        except ValueError as e:
            assert str(e) == "simulated provider failure"
    finally:
        current_request_id.reset(token)

    row = (
        db_session.query(ProviderCallTrace)
        .filter(ProviderCallTrace.provider == "fake_provider", ProviderCallTrace.operation == "fake_op_fails")
        .first()
    )
    assert row is not None
    assert row.success is False
    assert row.error_detail == "simulated provider failure"


def test_trace_provider_call_with_no_request_id_set_records_null(db_session):
    with trace_provider_call("fake_provider", "fake_op_no_request"):
        pass

    row = (
        db_session.query(ProviderCallTrace)
        .filter(ProviderCallTrace.provider == "fake_provider", ProviderCallTrace.operation == "fake_op_no_request")
        .first()
    )
    assert row is not None
    assert row.request_id is None


def test_provider_call_latency_summary_aggregates(db_session):
    with trace_provider_call("fake_provider", "fake_op_summary"):
        pass
    with trace_provider_call("fake_provider", "fake_op_summary"):
        pass
    try:
        with trace_provider_call("fake_provider", "fake_op_summary"):
            raise ValueError("boom")
    except ValueError:
        pass

    summary = provider_call_latency_summary(db_session, hours=24)
    row = next(s for s in summary if s["provider"] == "fake_provider" and s["operation"] == "fake_op_summary")
    assert row["count"] == 3
    assert row["failure_count"] == 1
    assert row["avg_duration_ms"] >= 0


def test_a_real_request_correlates_its_provider_trace_via_x_request_id(client, db_session, monkeypatch):
    """End-to-end proof of the whole mechanism: the middleware sets
    current_request_id for the lifetime of a real HTTP request, and a
    Provider Gateway call made downstream during that request picks it up
    without anything being passed explicitly - mocked at the vendor SDK
    boundary (stripe.identity.VerificationSession.create), not at
    create_verification_session itself, so the real trace_provider_call
    wrapper in app.integrations.kyc.stripe_identity actually runs."""
    from types import SimpleNamespace

    monkeypatch.setattr("app.core.config.settings.stripe_secret_key", "sk_test_fake")
    monkeypatch.setattr(
        "stripe.identity.VerificationSession.create",
        lambda **kwargs: SimpleNamespace(id="vs_trace_test", url="https://verify.stripe.com/x"),
    )

    token = _signup_and_login(client, "tracecorrelation@example.com")
    headers = {"Authorization": f"Bearer {token}"}
    case_response = client.post(
        "/compliance/cases", json={"jurisdiction": "US", "requirement_type": "kyc_individual"}, headers=headers
    )
    case_id = case_response.json()["id"]

    response = client.post(f"/compliance/cases/{case_id}/kyc/start", headers=headers)
    assert response.status_code == 200, response.text
    request_id = response.headers["X-Request-ID"]

    trace = (
        db_session.query(ProviderCallTrace)
        .filter(ProviderCallTrace.provider == "stripe_identity", ProviderCallTrace.operation == "create_verification_session")
        .order_by(ProviderCallTrace.created_at.desc())
        .first()
    )
    assert trace is not None
    assert trace.request_id == request_id
    assert trace.success is True


def _signup_and_login(client, email: str) -> str:
    client.post(
        "/auth/signup",
        json={"account_name": "Trace Test Co", "account_type": "individual", "email": email, "password": "supersecret123"},
    )
    token = client.post("/auth/login", json={"email": email, "password": "supersecret123"}).json()["access_token"]
    # A fresh signup defaults to the free trial - app.core.deps.
    # require_paid_or_read_only now blocks write actions (opening a
    # compliance case) for a TRIALING account, and this file's tests are
    # about provider-trace correlation, not trial-gating, so upgrade to a
    # real paid plan here rather than adding this to every individual test.
    client.put(
        "/billing/subscription/plan", json={"plan_code": "starter", "billing_period": "monthly"},
        headers={"Authorization": f"Bearer {token}"},
    )
    return token


def test_list_traces_requires_staff_auth(client):
    response = client.get("/ops/traces")
    assert response.status_code == 401


def test_list_traces_returns_recorded_traces(client, db_session):
    with trace_provider_call("test_provider_a", "op_a"):
        pass

    token = _create_and_login_staff(db_session, client, "tracestaff1@zoikolocal.com")
    response = client.get("/ops/traces", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert any(t["provider"] == "test_provider_a" and t["operation"] == "op_a" for t in response.json())


def test_list_traces_filters_by_provider(client, db_session):
    with trace_provider_call("test_provider_b", "op_b"):
        pass
    with trace_provider_call("test_provider_c", "op_c"):
        pass

    token = _create_and_login_staff(db_session, client, "tracestaff2@zoikolocal.com")
    response = client.get(
        "/ops/traces", params={"provider": "test_provider_b"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    providers = {t["provider"] for t in response.json()}
    assert providers == {"test_provider_b"}


def test_list_traces_filters_by_request_id(client, db_session):
    tok = current_request_id.set("req-filter-test")
    try:
        with trace_provider_call("test_provider_d", "op_d"):
            pass
    finally:
        current_request_id.reset(tok)
    with trace_provider_call("test_provider_d", "op_d_other_request"):
        pass

    token = _create_and_login_staff(db_session, client, "tracestaff3@zoikolocal.com")
    response = client.get(
        "/ops/traces", params={"request_id": "req-filter-test"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["operation"] == "op_d"


def test_trace_summary_requires_staff_auth(client):
    response = client.get("/ops/traces/summary")
    assert response.status_code == 401


def test_trace_summary_endpoint_returns_aggregated_rows(client, db_session):
    with trace_provider_call("test_provider_e", "op_e"):
        pass
    with trace_provider_call("test_provider_e", "op_e"):
        pass

    token = _create_and_login_staff(db_session, client, "tracestaff4@zoikolocal.com")
    response = client.get("/ops/traces/summary", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    row = next(r for r in response.json() if r["provider"] == "test_provider_e" and r["operation"] == "op_e")
    assert row["count"] == 2
    assert row["failure_count"] == 0
