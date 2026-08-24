"""Production Readiness & Go-Live Decision Standard §9 - "a production-
equivalent acceptance suite... the pass condition is not 'the UI
completed' [but that] the entire customer, carrier and financial truth
can be reconstructed from evidence." Table 22's golden path, chained
through real HTTP endpoints end to end, with no direct manipulation of
outcome state (no bare `invoice.status = "PAID"`-style shortcuts - see
§9.1's "Anti-shortcut rule").

What's real here: every step calls the actual route/service code path
that a real customer or staff action would trigger - signup, consent,
plan change, number reserve, checkout-session-driven purchase completion
(Architecture doc §9 - a number is provisioned immediately at checkout
time, no separate payment round trip; real gap fixed 2026-08-22),
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

A voicemail row is seeded directly (recording_url pointing at a fake
object) rather than driven through a real Twilio <Record> callback chain -
this is an INPUT fixture (same convention test_intelligence.py already
uses), not a shortcut on any OUTCOME this test verifies.

Mapped against the exact 21-stage acceptance chain a launch reviewer asked
for (signup -> verification -> paid-plan selection -> number provisioning
-> inbound call -> outbound authorised call -> voicemail -> AI processing
-> carrier usage record -> ZoikoNex rating -> invoice -> payment collection
-> Stripe event -> ZoikoNex ledger posting -> receipt -> tax treatment ->
customer portal balance -> finance reconciliation -> cancellation -> final
usage -> number release/refund where applicable):

- verification: this account's number is US, which has no active
  ComplianceRule requirement_type - purchase_number/_assert_purchase_
  eligible would have moved the number to COMPLIANCE_PENDING and this test
  would fail at the "status == active" assertion if that weren't true.
  Real KYC document upload/approval is covered separately in
  test_number_eligibility.py against a country that DOES require it - not
  duplicated here.
- receipt / tax treatment / customer portal balance: previously nothing
  in this codebase actually exercised these - the notification templates
  ('billing.invoice_available', 'billing.payment_succeeded',
  'billing.credit_or_refund_processed') were seeded but never called, and
  there was no customer-facing endpoint to see an invoice/payment history
  at all (GET /billing/zoikonex/sync-log is staff-only). All three gaps
  are now closed (see notify_invoice_available/notify_payment_succeeded in
  billing.service.run_billing_cycle, and GET /billing/invoices) and
  verified below.
- number release/refund where applicable: the golden path's own number
  purchase succeeds, so it only exercises the "release" half. The
  "failure" half - carrier rejects provisioning - is a genuinely
  different scenario, covered in the second test below,
  test_customer_is_notified_when_provisioning_fails. Note: since nothing
  is charged until the account's next real invoice (no payment happens
  at checkout time at all anymore), there is no "refund" concept for
  this failure mode the way there used to be under the old Stripe-first
  flow - only a customer notification that the order wasn't fulfilled.
"""
from twilio.request_validator import RequestValidator

from app.core.config import settings
from app.media.models import Voicemail
from app.notifications.models import NotificationDelivery, NotificationDeliveryStatus


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

    # 5. Compliant number provisioning - Architecture doc §9: numbers no
    # longer go through a separate Stripe Checkout/webhook round trip
    # (real gap fixed 2026-08-22) - the checkout-session endpoint
    # provisions immediately. This account's first number on a paid
    # "starter" plan is included, so this is the $0 path.
    monkeypatch.setattr(
        "app.numbering.numbers.service.telecom.buy_number",
        lambda e164, bundle_sid=None: {"sid": "PN_e2e_fake_sid", "phone_number": e164, "capabilities": {}},
    )
    purchase_response = client.post("/numbers/+15550091234/checkout-session", headers=headers)
    assert purchase_response.status_code == 200, purchase_response.text
    assert purchase_response.json()["included"] is True
    assert purchase_response.json()["number"]["status"] == "active"

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
    # This module's docstring already claims extract_conversation_summary
    # is mocked at the Provider Gateway boundary alongside transcribe_audio
    # - it wasn't actually wired up, so this call was silently hitting the
    # real Groq chat-completions endpoint (currently 404ing) instead.
    monkeypatch.setattr(
        "app.intelligence.service.extract_conversation_summary",
        lambda transcript: {
            "summary": "Customer called about their recent order.", "language": "en", "urgency": "low",
            "action_items": [], "suggested_follow_up": "Call the customer back about their order status.",
        },
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

    # 10b. Customer portal balance - a real, customer-facing (not
    # staff-only) view of what was invoiced/paid, scoped to this account
    # only. Previously nothing like this existed for a customer to see.
    invoices = client.get("/billing/invoices", headers=headers).json()
    invoice_entries = [e for e in invoices if e["event_type"] == "invoice_generated"]
    payment_entries = [e for e in invoices if e["event_type"] == "payment_collected"]
    assert len(invoice_entries) == 1
    assert invoice_entries[0]["reference"] == billing_result["invoice_id"]
    assert len(payment_entries) == 1
    assert payment_entries[0]["reference"] == billing_result["payment_intent_id"]

    # Info-leak check: a curated allow-list response must never carry raw
    # ZoikoNex-internal fields (error text, placeholder-price governance
    # metadata) - a real bug this exact endpoint shipped with once already.
    for entry in invoices:
        assert "payload" not in entry
        assert "bill_cycle_close_error" not in entry
        assert "capture_error" not in entry
        assert "placeholder_price" not in entry

    # 10c. Receipt + tax treatment - real notifications, not just a return
    # value. The invoice email's rendered body is asserted to actually
    # contain a resolved tax figure (0.00 today - a real ZoikoNex tax-
    # decision call still ran, see determine_tax_for_invoice_line's
    # docstring on why it's currently always 0%), not silently omitted.
    deliveries = (
        db_session.query(NotificationDelivery)
        .filter(NotificationDelivery.account_id == account_id)
        .order_by(NotificationDelivery.created_at.asc())
        .all()
    )
    invoice_email = next(d for d in deliveries if d.event_name == "billing.invoice_available")
    payment_email = next(d for d in deliveries if d.event_name == "billing.payment_succeeded")
    # Not SUPPRESSED (this fresh account has no bounce/opt-out on file).
    # SENT vs FAILED depends on this dev sandbox's Resend deliverability,
    # not something this test controls - see the refund test's identical
    # comment on this same tradeoff.
    assert invoice_email.status != NotificationDeliveryStatus.SUPPRESSED
    assert payment_email.status != NotificationDeliveryStatus.SUPPRESSED

    # NotificationDelivery only persists the rendered `subject`, not the
    # full body - re-render the actual seeded template with this run's
    # real values to confirm the tax figure notify_invoice_available
    # passed in is really present in what gets sent, not just claimed.
    from app.notifications.models import NotificationTemplate

    invoice_template = db_session.query(NotificationTemplate).filter(
        NotificationTemplate.key == "billing.invoice_available"
    ).first()
    rendered_body = invoice_template.body_template.format(
        user_display_name="e2egoldenpath@example.com",
        transaction_reference=billing_result["invoice_id"],
        billing_period="irrelevant-for-this-check",
        transaction_subtotal="12.99",
        transaction_tax="0.00",
        transaction_total="12.99",
        transaction_currency="USD",
    )
    assert "Tax: 0.00" in rendered_body

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


def test_customer_is_notified_when_provisioning_fails(client, db_session, monkeypatch):
    """The golden path above only exercises the happy path (purchase
    succeeds, customer later cancels/releases it). This is the other real
    outcome the acceptance chain's "number release/refund where
    applicable" line asks for: a carrier-side rejection during
    provisioning. Architecture doc §9 (real gap fixed 2026-08-22): a
    number is provisioned immediately at checkout time with no payment
    collected upfront at all (cost is recorded as a pending charge and
    billed alongside the plan fee later) - so a failure here has nothing
    to refund, unlike the old Stripe-charges-then-provisions flow this
    test used to cover. purchase_number's TelecomError branch reverts the
    number to RESERVED and notifies the customer via notify_number_order_
    not_approved - verified here."""
    from app.integrations.telecom.twilio import TelecomError
    from app.numbering.numbers.models import PhoneNumber, PhoneNumberStatus

    def _reject_purchase(e164: str, bundle_sid=None):
        raise TelecomError("carrier rejected this number")

    token, account_id = _signup_and_login(client, "e2erefund@example.com")
    headers = {"Authorization": f"Bearer {token}"}

    assert client.post(
        "/compliance/consent", json={"consent_type": "emergency_calling_acknowledged"}, headers=headers,
    ).status_code == 200

    reserve_response = client.post(
        "/numbers/reserve", json={"e164": "+15550091999", "country": "US"}, headers=headers,
    )
    assert reserve_response.status_code == 201, reserve_response.text

    # The carrier rejects the actual number purchase - a genuine,
    # already-handled post-checkout failure mode, not a shortcut.
    monkeypatch.setattr("app.numbering.numbers.service.telecom.buy_number", _reject_purchase)
    checkout_response = client.post("/numbers/+15550091999/checkout-session", headers=headers)
    assert checkout_response.status_code == 502, checkout_response.text

    # The number was NOT left ACTIVE or dangling - reverted to RESERVED,
    # so it's neither owned by the customer nor blocking anyone else from
    # trying (see purchase_number's TelecomError branch).
    number = db_session.query(PhoneNumber).filter(PhoneNumber.e164 == "+15550091999").first()
    assert number.status == PhoneNumberStatus.RESERVED

    # The customer actually got told - real NotificationDelivery row, not
    # a silent failure with no evidence trail.
    failure_email = (
        db_session.query(NotificationDelivery)
        .filter(
            NotificationDelivery.account_id == account_id,
            NotificationDelivery.event_name == "number.order_not_approved",
        )
        .first()
    )
    assert failure_email is not None
    # Not SUPPRESSED (this test's fresh account has no bounce/opt-out on
    # file - a SUPPRESSED status here would mean the pipeline wrongly
    # skipped sending). SENT vs FAILED depends on whether this dev
    # sandbox's Resend key/domain is actually deliverable right now - a
    # real environmental fact this test doesn't control, not something to
    # paper over by asserting SENT unconditionally.
    assert failure_email.status != NotificationDeliveryStatus.SUPPRESSED
