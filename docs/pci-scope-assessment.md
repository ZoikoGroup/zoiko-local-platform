# PCI Scope Assessment (Engineering Draft)

Written to close one of the Commercial Billing Operating Standard doc's (§19,
"O1"/PCI section) open asks: "PCI scope documentation." This is an
**engineering-level technical assessment of the current architecture**, not a
legal or compliance sign-off — a qualified PCI-DSS assessor (or QSA, if one is
ever required) needs to review and formally accept this before it's relied on
for an actual audit, a payment-processor application, or a customer security
questionnaire.

**Revised twice.** First, after `app/integrations/billing/stripe_checkout.py`
shipped — this doc's original version (see git history) predates that
integration and concluded Zoiko Local was out of PCI scope *because no
payment collection existed at all*. That's no longer the case: real payment
collection exists today, for one specific flow. Second, after
`app/integrations/billing/zoikonex.py` stopped being a mock — it's now a
real, tested client (built and tested end-to-end against a locally
self-hosted copy of the ZoikoNex backend, not a live production ZoikoNex
instance — see that file's own docstring for exactly what's been tested and
what two ZoikoNex-side bugs still block full end-to-end billing). The
conclusion below is still favorable, but the reasoning for the ZoikoNex leg
has changed from "no card concept because nothing is real" to "no card
concept because even the real integration only ever handles a tokenized
placeholder, never a card number."

## Bottom line

**Zoiko Local is in minimal PCI-DSS scope (SAQ-A territory, for a QSA to
confirm) for the one real payment flow that exists — number-purchase
checkout — and out of scope for everything else**, including subscription/
plan billing: that flow is now a real, tested ZoikoNex client rather than a
mock, but it still never handles a real card number (see below), and isn't
connected to a live ZoikoNex production instance regardless.

## Why

Per the Commercial Billing Operating Standard doc's three-record-separation
doctrine: Zoiko Local owns *service* truth (numbers, calls, AI state) and
never invents a price or touches money itself. `app/billing/`
(`Subscription`, `Plan`) still has **no price fields** — see `Plan`'s
docstring: "No price fields — no payment processing exists here at all."

For subscription/plan billing, `app/integrations/billing/zoikonex.py` is a
**real, tested integration** as of 2026-08 (OAuth2 client_credentials
against ZoikoNex's own identity-tenancy service, real HTTP calls, real
payment-intent creation/authorization tested end-to-end against ZoikoNex's
own dev-only simulated payment gateway) — it is no longer accurate to call
this a mock, and this doc's earlier "mock adapter... no concept of a card"
language is stale. What hasn't changed, and is the actual reason this stays
out of PCI scope: `create_payment_intent` (`zoikonex.py`, around line 897)
sends a hardcoded `payment_method_token: "pm_test_card"` — a pre-tokenized
placeholder, by explicit design ("real card data is never accepted or
stored anywhere in this codebase," per that function's own comment) — not a
real card number collected from a customer. No frontend form, API payload,
or database column anywhere in this flow carries cardholder data. Separately
and independently of the PCI question: this integration is also not yet
connected to any live production ZoikoNex instance (only tested against a
local self-hosted copy) — a real gap, but a connectivity/credentials one,
not a card-data-handling one.

Number purchases are different: `app/integrations/billing/stripe_checkout.py`
is a **real, live-tested integration against a real (test-mode) Stripe
account** — the first real payment collection anywhere in this codebase,
wired into `app/numbering/numbers/service.py`. Per the recommendation this
doc already made before that integration was built, it follows the
hosted/redirect pattern exactly:

- `create_checkout_session` calls `stripe.checkout.Session.create(mode="payment", ...)`
  and returns the Stripe-hosted `session.url` — the customer is redirected to
  a page Stripe itself serves and controls.
- No file outside `stripe_checkout.py` (the sole Provider Gateway for this
  vendor/product) imports the `stripe` SDK for payments, and nothing in
  `frontend/src/app/` renders a card-entry form, embeds a hosted payment
  field/iframe, or otherwise collects card data client-side.
- `refund_payment` and `construct_webhook_event` operate on Stripe's own
  opaque identifiers (`payment_intent_id`, signed webhook payloads) — never
  a raw card number.

Concretely, nothing in this repo, including the new checkout flow:

- Renders a card-entry form or embeds a payment processor's hosted
  fields/iframe directly (the customer leaves for Stripe's own domain
  instead)
- Accepts, parses, or validates a card number, CVV, or expiration date
- Stores anything resembling cardholder data in the database (no such
  columns exist on any model — Stripe's session/payment-intent IDs are
  opaque tokens, not cardholder data)
- Transmits cardholder data to any internal system — Stripe's webhook
  payloads carry payment status and metadata, never card details

## What would change this assessment

- **The ZoikoNex payment-intent flow moving off its hardcoded
  `pm_test_card` token onto anything that accepts a real card number or
  payment-method reference from a customer** — redo this assessment for
  that flow specifically when it happens. If it follows the same hosted/
  redirect or client-side tokenization pattern as `stripe_checkout.py`, the
  outcome should look similar; if not, re-derive from first principles.
  (Connecting the existing ZoikoNex client to a live production ZoikoNex
  instance, on its own, does NOT change this assessment — same
  `pm_test_card`-style tokenized call shape either way, unless the payload
  itself changes.)
- **Any flow, existing or new, where a card number is submitted to, or
  passes through, a Zoiko Local server or database** — full PCI-DSS scope
  would apply to that server. Nothing today does this; keep it that way.
- **Formal SAQ type determination** — "SAQ-A territory" above is an
  engineering estimate based on the hosted-redirect pattern, not a
  determination a QSA or acquiring bank has made. That requires an actual
  PCI-DSS assessor engagement before this is relied on for a real audit,
  processor application, or customer security questionnaire.

## Recommendation

Keep requiring hosted/tokenized card entry as a locked architectural
decision for every future payment flow (subscription billing included) — the
same "buy infrastructure, own orchestration" posture this codebase already
applies to CPaaS, video, and transcription, and the one `stripe_checkout.py`
already follows. Get a qualified PCI-DSS assessor involved before any new
payment integration ships, and before this document is relied on for a real
audit.
