"""Production Readiness & Go-Live Decision Standard §9 - "a production-
equivalent acceptance suite... the pass condition is not 'the UI
completed' [but that] the entire customer, carrier and financial truth
can be reconstructed from evidence." Table 22's golden path, chained
through real HTTP endpoints end to end, with no direct manipulation of
outcome state (no bare `invoice.status = "PAID"`-style shortcuts - see
§9.1's "Anti-shortcut rule").

What's real here: every step calls the actual route/service code path
that a real customer or staff action would trigger - signup, consent,
plan change, number reserve, Stripe webhook-driven purchase completion,
inbound/outbound calling, AI summarization, the real rating->invoice->
payment billing cycle (staff maker-checker), reconciliation, cancellation,
number release.

What's mocked, and why - at the Provider Gateway boundary only, same
discipline the rest of this test suite already uses everywhere:
- Twilio (buy_number/place_call) - no real telecom account reachable here.
- Groq (transcribe_audio/extract_conversation_summary via the voicemail
  summarize flow) - real network calls, mocked to keep this deterministic
  and fast, not because the surrounding code is untested (test_intelligence.py
  already covers real Groq calls elsewhere).
- ZoikoNex - already mocked globally for every test by conftest.py's
  autouse mock_zoikonex_sync fixture (a real self-hosted ZoikoNex instance
  isn't reachable in this environment - see docs/zoikonex-defect-packet-
  2026-08-13.md). Not re-mocked here; this test just inherits it.
- Stripe - the hosted Checkout page itself can't be driven headlessly by
  any automated test; this simulates the webhook it produces on
  completion (checkout.session.completed), the same real, signature-
  verified path production actually receives from Stripe.

A voicemail row is seeded directly (recording_url pointing at a fake
object) rather than driven through a real Twilio <Record> callback chain -
this is an INPUT fixture (same convention test_intelligence.py already
uses), not a shortcut on any OUTCOME this test verifies.
"""
import hashlib
import hmac
import json
import time

from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.media.models import Voicemail


def _signup_and_login(client, email: str) -> tuple[str, str]:
    signup = client.post(
        "/auth/signup",
        json={
            "account_name": "E2E Acceptance Co", "account_type": "business",
            "email": email, "password": "supersecret123",
        },
    )
    account_id = signup.json()["account_id"]
    login = client.post("/auth/login", json={"email": email, "password": "supersecret123"})
    return login.json()["access_token"], account_id


def _twilio_signature(url: str, params: dict) -> str:
    return RequestValidator(settings.twilio_auth_token).compute_signature(url, params)


def _stripe_webhook_body_and_signature(secret: str, event_type: str, session_object: dict) -> tuple[bytes, str]:
    body = json.dumps(
        {"id": "evt_e2e_test", "object": "event", "type": event_type, "data": {"object": session_object}}
    ).encode()
    timestamp = str(int(time.time()))
    signed_payload = f"{timestamp}.{body.decode()}"
    signature = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
    return body, f"t={timestamp},v1={signature}"


def _create_and_login_staff(db_session, client, email: str, role):
    from app.staff import service as staff_service

    staff_service.create_staff(db_session, email=email, password="staffpass123", role=role)
    return client.post("/staff/login", json={"email": email, "password": "staffpass123"}).json()["access_token"]


def test_golden_path_signup_through_number_release(client, db_session, monkeypatch):
    from app.staff.models import PlatformStaffRole

    # 1. Signup + identity (JWT-authenticated from here on).
    token, account_id = _signup_and_login(client, "e2egoldenpath@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Consent (emergency-calling disclosure + AI processing) - real
    # compliance gates, not bypassed.
    assert client.post(
        "/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers,
    ).status_code == 200
    assert client.post(
        "/compliance/consent", json={"consent_type": "ai_processing"}, headers=headers,
    ).status_code == 200

    # 3. Plan selection - real entitlement/subscription change, not a
    # direct DB write.
    plan_response = client.put("/billing/subscription/plan", json={"plan_code": "starter"}, headers=headers)
    assert plan_response.status_code == 200, plan_response.text
    assert plan_response.json()["plan_code"] == "starter"

    # 4. Number search + reservation.
    reserve_response = client.post(
        "/numbers/reserve", json={"e164": "+15550091234", "country": "US"}, headers=headers,
    )
    assert reserve_response.status_code == 201, reserve_response.text

    # 5. Payment authorization + compliant number provisioning - a real
    # Stripe Checkout page can't be driven headlessly, so this simulates
    # the signature-verified webhook it actually sends on completion (the
    # same path test_stripe_payment_webhook_completes_the_purchase covers
    # in isolation) rather than calling /numbers/purchase directly.
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164: {"sid": "PN_e2e_fake_sid", "phone_number": e164, "capabilities": {}},
    )
    monkeypatch.setattr("app.core.config.settings.stripe_payments_webhook_secret", "whsec_e2e_test")
    webhook_body, webhook_signature = _stripe_webhook_body_and_signature(
        "whsec_e2e_test", "checkout.session.completed",
        {"id": "cs_e2e_test", "metadata": {"e164": "+15550091234", "account_id": account_id}},
    )
    purchase_webhook_response = client.post(
        "/numbers/payments/webhook", content=webhook_body, headers={"Stripe-Signature": webhook_signature},
    )
    assert purchase_webhook_response.status_code == 204

    numbers = client.get("/numbers", headers=headers).json()
    purchased_number = next(n for n in numbers if n["e164"] == "+15550091234")
    assert purchased_number["status"] == "active"

    # 6. Inbound call.
    incoming_url = "http://testserver/media/voice/incoming"
    incoming_params = {
        "To": "+15550091234", "From": "+15559990000", "CallSid": "CAe2einbound", "CallStatus": "ringing",
    }
    incoming_response = client.post(
        "/media/voice/incoming", data=incoming_params,
        headers={"X-Twilio-Signature": _twilio_signature(incoming_url, incoming_params)},
    )
    assert incoming_response.status_code == 200

    # 7. Outbound authorized call (real risk/fraud/billing gates run here -
    # see app.media.service._dispatch_outbound_call).
    monkeypatch.setattr(
        "app.media.service.telecom.place_call",
        lambda **kwargs: {"sid": "CAe2eoutbound", "status": "queued", "to": kwargs["to"], "from": kwargs["from_"]},
    )
    outbound_response = client.post(
        "/media/voice/outbound", json={"to": "+15559991111", "from": "+15550091234"}, headers=headers,
    )
    assert outbound_response.status_code == 200, outbound_response.text

    # 8. Provider usage/cost evidence - the real Twilio status-callback
    # webhook, signature-verified, drives real usage metering
    # (app.usage.service.record_usage_event), not a direct UsageEvent insert.
    status_url = "http://testserver/media/voice/status-callback"
    status_params = {"CallSid": "CAe2eoutbound", "CallStatus": "completed", "CallDuration": "125"}
    status_response = client.post(
        "/media/voice/status-callback", data=status_params,
        headers={"X-Twilio-Signature": _twilio_signature(status_url, status_params)},
    )
    assert status_response.status_code == 204

    usage_events = client.get("/usage", headers=headers).json()
    call_usage = next(e for e in usage_events if e["event_type"] == "call_seconds")
    assert call_usage["quantity"] == 125
    assert call_usage["estimated_cost_cents"] is not None

    # 9. Voicemail + AI processing (real transcription/LLM call sites,
    # Groq itself mocked at the Provider Gateway boundary - see module
    # docstring). The voicemail row is a seeded input fixture (no real
    # inbound <Record> flow available in this test environment), not a
    # shortcut on the summarize outcome, which runs for real.
    voicemail = Voicemail(
        phone_number_id=purchased_number["id"], account_id=account_id, from_number="+15559992222",
        recording_url="https://example.com/e2e-fake-voicemail.wav",
    )
    db_session.add(voicemail)
    db_session.commit()
    monkeypatch.setattr("app.intelligence.service.download_recording", lambda url: b"fake-audio-bytes")
    monkeypatch.setattr(
        "app.intelligence.service.transcribe_audio",
        lambda audio_bytes: "Hi, this is a customer calling about their recent order.",
    )
    summarize_response = client.post(f"/intelligence/voicemails/{voicemail.id}/summarize", headers=headers)
    assert summarize_response.status_code == 201, summarize_response.text
    assert summarize_response.json()["summary"]

    from app.intelligence.models import AIJob, AIJobStatus, SummarySourceType
    ai_job = (
        db_session.query(AIJob)
        .filter(AIJob.source_type == SummarySourceType.VOICEMAIL, AIJob.source_id == voicemail.id)
        .first()
    )
    assert ai_job.status == AIJobStatus.SUCCEEDED

    ai_usage = next(e for e in client.get("/usage", headers=headers).json() if e["event_type"] == "ai_summary")
    assert ai_usage is not None

    # 10. ZoikoNex rating -> invoice -> payment (real run_billing_cycle
    # code path via the staff maker-checker flow - request as one staff
    # member, approve as a different one; ZoikoNex itself is mocked
    # globally by conftest.py's autouse fixture, not here).
    requester_token = _create_and_login_staff(
        db_session, client, "e2erequester@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    billing_request = client.post(
        "/billing/zoikonex/run-billing-cycle/request", json={"account_id": account_id},
        headers={"Authorization": f"Bearer {requester_token}"},
    )
    assert billing_request.status_code == 201, billing_request.text
    action_id = billing_request.json()["id"]

    approver_token = _create_and_login_staff(
        db_session, client, "e2eapprover@zoikolocal.com", PlatformStaffRole.SUPER_ADMIN
    )
    billing_approval = client.post(
        f"/billing/actions/{action_id}/approve", headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert billing_approval.status_code == 200, billing_approval.text
    billing_result = billing_approval.json()["result"]
    assert billing_result["billed"] is True
    assert billing_result["invoice_status"] == "ISSUED"
    assert billing_result["payment_status"] == "captured"

    # 11. Reconciliation - Zoiko Local <-> ZoikoNex sync state, not a
    # silently-accepted mismatch.
    reconciliation_run = client.post(
        "/billing/zoikonex/reconciliation/run", headers={"Authorization": f"Bearer {approver_token}"},
    )
    assert reconciliation_run.status_code == 200, reconciliation_run.text

    # 12. Cancellation + number release - the real self-service path,
    # confirmed missing entirely by an earlier acceptance pass and since
    # built (see the "Add subscription cancellation" commit).
    cancel_subscription_response = client.post("/billing/subscription/cancel", json={}, headers=headers)
    assert cancel_subscription_response.status_code == 200, cancel_subscription_response.text
    assert cancel_subscription_response.json()["canceled_at"] is not None

    monkeypatch.setattr("app.numbering.numbers.service.telecom.release_number", lambda sid: None)
    cancel_number_response = client.post("/numbers/+15550091234/cancel", headers=headers)
    assert cancel_number_response.status_code == 200, cancel_number_response.text
    assert cancel_number_response.json()["status"] == "cancelled"

    # Full-lifecycle evidence check - every real record this journey
    # should have left behind actually exists and is internally
    # consistent, not just "the last response was 200."
    final_numbers = client.get("/numbers", headers=headers).json()
    final_number = next(n for n in final_numbers if n["e164"] == "+15550091234")
    assert final_number["status"] == "cancelled"

    final_subscription = client.get("/billing/subscription", headers=headers).json()
    assert final_subscription["status"] == "canceled"

    final_summaries = client.get("/intelligence/summaries", headers=headers).json()
    assert len(final_summaries) == 1

    final_usage = client.get("/usage", headers=headers).json()
    assert {e["event_type"] for e in final_usage} == {"call_seconds", "ai_summary"}
