# PCI Scope Assessment (Engineering Draft)

Written to close one of the Commercial Billing Operating Standard doc's (§19,
"O1"/PCI section) open asks: "PCI scope documentation." This is an
**engineering-level technical assessment of the current architecture**, not a
legal or compliance sign-off — a qualified PCI-DSS assessor (or QSA, if one is
ever required) needs to review and formally accept this before it's relied on
for an actual audit, a payment-processor application, or a customer security
questionnaire.

## Bottom line

**Zoiko Local, as built today, is out of PCI-DSS scope entirely.** No
component in this codebase collects, transmits, processes, or stores
cardholder data (PAN, cardholder name, expiration date, service code) or
sensitive authentication data (CVV, PIN, full track data) — because no
component in this codebase does anything with payments at all yet.

## Why

Per the Commercial Billing Operating Standard doc's three-record-separation
doctrine, already reflected in the code: Zoiko Local owns *service* truth
(numbers, calls, AI state) and explicitly never invents a price or touches
money. `app/billing/` (`Subscription`, `Plan`) has **no price fields** — see
`Plan`'s docstring: "No price fields — no payment processing exists here at
all." The only billing integration point,
`app/integrations/billing/zoikonex.py`, is an explicitly-disclosed **mock**:
it generates fake reference IDs locally, makes no HTTP calls, and has no
concept of a card, a payment method, or a charge. There is no payment form,
no card-number input field, and no checkout flow anywhere in
`frontend/src/app/`.

Concretely, nothing in this repo:

- Renders a card-entry form or embeds a payment processor's hosted
  fields/iframe (Stripe Elements, Braintree Drop-in, etc.)
- Accepts, parses, or validates a card number, CVV, or expiration date
- Stores anything resembling cardholder data in the database (no such
  columns exist on any model)
- Transmits cardholder data to any internal or external system

## What changes this assessment

The moment a real payment collection flow is built — whether that's ZoikoNex
itself becoming a real, callable service, or a payment processor (Stripe,
Braintree, Adyen, etc.) being integrated directly — this assessment must be
redone. The scope outcome will depend entirely on *how* that integration is
built:

- **Hosted/redirect or client-side tokenization** (the processor's own
  hosted checkout page, or a client-side SDK like Stripe Elements/Payment
  Element that tokenizes the card in the browser before it ever reaches
  Zoiko Local's servers) — Zoiko Local's backend still never touches raw
  card data, keeping scope minimal (typically SAQ-A or SAQ-A-EP territory,
  for a QSA to confirm).
- **Any flow where a card number is submitted to, or passes through, a
  Zoiko Local server or database** — full PCI-DSS scope applies to that
  server, and this is the outcome to actively design away from per the
  Provider Gateway doctrine already established in this codebase (buy
  payment infrastructure, don't build it).

## Recommendation

When real payment collection is designed (real ZoikoNex integration or a
direct processor integration), require hosted/tokenized card entry as a
locked architectural decision from day one — the same "buy infrastructure,
own orchestration" posture this codebase already applies to CPaaS, video,
and transcription. Get a qualified PCI-DSS assessor involved before that
integration ships, not after.
