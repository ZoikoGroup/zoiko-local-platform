# PCI Scope Assessment (Engineering Draft)

Written to close one of the Commercial Billing Operating Standard doc's (§19,
"O1"/PCI section) open asks: "PCI scope documentation." This is an
**engineering-level technical assessment of the current architecture**, not a
legal or compliance sign-off — a qualified PCI-DSS assessor (or QSA, if one is
ever required) needs to review and formally accept this before it's relied on
for an actual audit, a payment-processor application, or a customer security
questionnaire.

**Revised** after `app/integrations/billing/stripe_checkout.py` shipped —
this doc's original version (see git history) predates that integration and
concluded Zoiko Local was out of PCI scope *because no payment collection
existed at all*. That's no longer the case: real payment collection exists
today, for one specific flow. The conclusion below is still favorable, but
for a different reason.

## Bottom line

**Zoiko Local is in minimal PCI-DSS scope (SAQ-A territory, for a QSA to
confirm) for the one real payment flow that exists — number-purchase
checkout — and out of scope for everything else**, which still has no
payment collection at all (subscription/plan billing continues to route
through the disclosed mock ZoikoNex adapter, with no card, payment method,
or charge concept).

## Why

Per the Commercial Billing Operating Standard doc's three-record-separation
doctrine: Zoiko Local owns *service* truth (numbers, calls, AI state) and
never invents a price or touches money itself. `app/billing/`
(`Subscription`, `Plan`) still has **no price fields** — see `Plan`'s
docstring: "No price fields — no payment processing exists here at all." For
subscription/plan billing, `app/integrations/billing/zoikonex.py` remains an
explicitly-disclosed **mock**: fake reference IDs, no HTTP calls, no concept
of a card. That part of this assessment is unchanged.

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

- **Subscription/plan billing gaining real payment collection** (ZoikoNex
  becoming a real, callable service, or a direct processor integration for
  recurring billing) — redo this assessment for that flow specifically when
  it's built. If it follows the same hosted/redirect or client-side
  tokenization pattern as `stripe_checkout.py`, the outcome should look
  similar; if not, re-derive from first principles.
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
