# ZoikoNex Defect Packet — Payment Capture & Bill-Cycle Close

**Prepared by:** Zoiko Local Engineering
**Date:** 2026-08-13
**Priority:** P0 — financial-integrity blocker per the Zoiko Local Production Readiness & Go-Live Decision Standard, §7
**Status:** Both defects are reproducible on demand against a self-hosted ZoikoNex instance (github.com/Zoiko-Nex/backend). Neither is caused by Zoiko Local's client code — both are confirmed via ZoikoNex's own server-side error responses/logs, not inferred from client-side symptoms.

This packet covers two independent defects in two different ZoikoNex services. They are unrelated to each other and should be triaged separately.

---

## Defect 1 of 2 — Payment capture fails on every attempt (payments service)

### Exact failing workflow
`POST /v1/payment-intents/{id}/capture` against ZoikoNex's `payments` service, called from Zoiko Local's `capture_payment_intent()` (`backend/app/integrations/billing/zoikonex.py`), as the third step of the payment-intent lifecycle: **create → authorise → capture**. Create and authorise both succeed every time; capture fails every time.

### Expected vs. observed state transition
| Step | Expected | Observed |
|---|---|---|
| `POST /v1/payment-intents` | `status: CREATED` | ✅ Matches |
| `POST /v1/payment-intents/{id}/authorise` | `status: AUTHORISED` | ✅ Matches |
| `POST /v1/payment-intents/{id}/capture` | `status: CAPTURED` | ❌ Request fails server-side before a status is ever returned |

The intent never reaches `CAPTURED` in this environment — every downstream capability that depends on a captured payment (refunds against a real capture, settlement, payout reconciliation) has never been exercisable end-to-end as a result.

### Root cause (from ZoikoNex's own error response/logs)
The `payments` service's capture handler calls its evidence-ledger gRPC client with a request object that does not satisfy Go's `proto.Message` interface:

```
failed to marshal, message is *evidenceclient.pbAppendRequest, want proto.Message
```

This points to `payments`' evidence-ledger client wrapper constructing (or being passed) the wrong concrete type for the gRPC call — a `pbAppendRequest` that isn't being marshaled through the expected proto interface. This is a server-side code defect in the `payments` ↔ evidence-ledger integration, not a client request-shape issue: the request body Zoiko Local sends (see below) matches `payments`' own `API.INTEGRATION.md`, and the error is thrown before any evidence-ledger business logic runs.

### Request sent (sanitized — no real card data, tokenized payment method only)
```
POST /v1/payment-intents/{payment_intent_id}/capture
Idempotency-Key: pay-capture-{payment_intent_id}
Authorization: Bearer <OAuth2 client_credentials JWT>
```
No body. `payment_intent_id` is the id returned by the preceding `create` call for this invoice's payment intent. Payment method on the underlying intent is `pm_test_card` (ZoikoNex's own dev-only simulated-gateway test token — real card data is never sent by Zoiko Local at any point, per our PCI boundary).

### Financial impact class
**Charge succeeds but ledger fails** (partial) — more precisely, authorization succeeds (funds are reserved/held), but capture — the step that would actually collect them — never completes. No customer is ever incorrectly charged; the practical effect is that Zoiko Local can authorize a payment but never actually collect it through ZoikoNex.

### Reproducibility
**Deterministic — fails on every attempt**, with any account, invoice, or amount, in this environment. Not intermittent.

### Repro steps
1. Stand up ZoikoNex's `payments` + `identity-tenancy` + `customer-account` services locally (or point at any environment with the same evidence-ledger gRPC wiring).
2. Create a customer/account, an invoice, and a payment intent (`POST /v1/payment-intents`) for that invoice.
3. Authorise it (`POST /v1/payment-intents/{id}/authorise`) — succeeds.
4. Capture it (`POST /v1/payment-intents/{id}/capture`) — fails with the marshaling error above.

### Zoiko Local's own regression coverage (already passing, protecting our side of this)
Zoiko Local does not treat this as a hard failure — it records "authorized but not captured" and continues, rather than silently pretending collection succeeded. That contract is regression-tested today:

`backend/tests/test_billing_cycle.py::test_run_billing_cycle_handles_capture_failure_gracefully` — simulates exactly this failure mode and asserts `result["captured"] is False` and `result["capture_error"]` is populated while the rest of the billing cycle (invoice issuance) proceeds normally. This test passes today because Zoiko Local degrades gracefully — it does **not** and cannot prove ZoikoNex's bug is fixed. The exit criterion for this defect is a real, live `capture` call reaching `status: CAPTURED`, not a passing test on our side.

### What we need from ZoikoNex
- Root-cause fix in `payments`' evidence-ledger gRPC client (correct `proto.Message` construction for the append request).
- A build/version reference we can point our client at once fixed.
- Confirmation of the exact request/response contract if it changes as part of the fix.

---

## Defect 2 of 2 — Bill-cycle close (and plain read) fails on every bill cycle (billing-invoice service)

### Exact failing workflow
Both `POST /v1/bill-cycles/{id}/close` and plain `GET /v1/bill-cycles/{id}` against ZoikoNex's `billing-invoice` service, called from Zoiko Local's `close_bill_cycle()` (`backend/app/integrations/billing/zoikonex.py`). Both endpoints route through the same internal `GetBillCycle` repository function, so both fail identically — this is a read-path bug, not something specific to the close action.

### Expected vs. observed state transition
| Step | Expected | Observed |
|---|---|---|
| `POST /v1/bill-cycles` (open) | `status: OPEN` | ✅ Matches |
| Usage rated against the open cycle | accumulates normally | ✅ Matches |
| `POST /v1/bill-cycles/{id}/close` | `status: CLOSED` | ❌ Request fails server-side, every time, for every bill cycle created in this environment |
| `GET /v1/bill-cycles/{id}` (plain read, unrelated to close) | Returns the bill cycle object | ❌ Same failure — confirms this is a `GetBillCycle` bug, not a close-specific one |

### Root cause (from ZoikoNex's own error response)
```
postgres.GetBillCycle: can't scan into dest[12]: cannot scan NULL into *string
```
`GetBillCycle`'s SQL row-scan targets a `*string` (non-nullable Go type) for column index 12, but that column is `NULL` in the actual row. Likely candidate: a nullable column such as `jurisdiction_code` that comes back empty (`""`) rather than genuinely being set on bill-cycle creation — but that's our best inference from behavior, not something we can confirm without visibility into ZoikoNex's own schema/query. **Every bill cycle created in this environment hits this**, which suggests the offending column is either never populated on creation or the scan target should be nullable (`sql.NullString` / `*sql.NullString` in Go) regardless.

### Request sent
```
POST /v1/bill-cycles/{bill_cycle_id}/close
Idempotency-Key: bill-cycle-close-{bill_cycle_id}
Authorization: Bearer <OAuth2 client_credentials JWT>
```
No body required by this endpoint per `billing-invoice`'s own `API.INTEGRATION.md`.

### Financial impact class
**Other** — no charge is affected directly, but a bill cycle that can never close means the billing period it represents can never be formally finalized on ZoikoNex's side, even though invoicing against it (via the separate `/v1/invoices` endpoints) still works. This is a reconciliation/finalization gap: Zoiko Local has an invoice it correctly created and issued, but the parent bill cycle stays open forever from ZoikoNex's perspective.

### Reproducibility
**Deterministic — fails on every bill cycle**, confirmed on both the `close` action and a plain `GET` read of the same resource. Not intermittent.

### Repro steps
1. Open a bill cycle: `POST /v1/bill-cycles` with a valid `account_id`/`customer_id`.
2. Immediately try either `GET /v1/bill-cycles/{id}` or `POST /v1/bill-cycles/{id}/close` — both fail with the scan error above.

### Zoiko Local's own regression coverage (already passing)
`backend/tests/test_billing_cycle.py::test_run_billing_cycle_handles_bill_cycle_close_failure_gracefully` — simulates this exact failure and asserts the billing cycle (invoice issuance) still completes (`result["invoice_status"] == "ISSUED"`) while `result["bill_cycle_closed"] is False` and `result["bill_cycle_close_error"]` is populated. Same caveat as Defect 1: this proves Zoiko Local degrades gracefully, not that ZoikoNex's bug is fixed.

### What we need from ZoikoNex
- Identify which column at row index 12 in the `GetBillCycle` query is nullable in the table but non-nullable in the Go scan target, and either populate it on creation or fix the scan type.
- A build/version reference we can point our client at once fixed.

---

## Fields we cannot fill in from this environment right now

Per the Production Readiness Standard's required defect-packet fields, the following need a **live repro run** against a running ZoikoNex instance to capture (this environment currently has no ZoikoNex services reachable — all of `localhost:8080–8096` are down):
- Fresh UTC timestamps for a specific failing call
- The specific `payment_intent_id` / `bill_cycle_id` / ZoikoNex correlation-trace ID / tenant-customer-account IDs for that specific call
- Sanitized full request/response JSON bodies captured from that live call

We're flagging this rather than inventing placeholder IDs. The repro steps above are exact and were confirmed against a real self-hosted ZoikoNex instance when this client was built — re-running them against any live ZoikoNex environment (ours or ZoikoNex's own) will produce fresh evidence immediately; both failures are deterministic, not timing-dependent.

## Acceptance / exit criterion
Per the Production Readiness Standard §7.3: this defect is closed only when a live `capture` call reaches `CAPTURED` (Defect 1) and a live `close` call reaches `CLOSED` (Defect 2) end-to-end, with Zoiko Local's own regression tests (above) continuing to pass as documentation of the graceful-degradation path that existed while these bugs were open.
