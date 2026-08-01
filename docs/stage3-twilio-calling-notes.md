# Stage 3 Prep — Twilio Voice (Calling) Notes

Same spirit as `docs/stage2-twilio-numbering-notes.md`: research + a reference script,
no Stage 3 app code. Stage 3 (Voice Routing) lives in `backend/app/media/` once it
actually starts, and depends on Stage 2 (a `Local Number` must reach `Active` status)
before it has a real "From" number to route calls through.

Reference script: `scripts/twilio_calling_explore.py` (`list`, `status`, `call`
subcommands).

## Confirmed live

- `GET /Calls.json` (list call logs) — works with **zero owned numbers and zero calls
  made**: returns `200` with an empty list, not an error. Same empty-is-not-an-error
  pattern as the numbering search API.

## Documented, not executed

- `POST /Calls.json` (place an outbound call) — requires **`From` to be a Twilio number
  owned on this account**. This trial account owns zero numbers (see Stage 2 notes), so
  this couldn't be exercised live without first buying a number, which was intentionally
  skipped to avoid spending trial credit.
  - `From` is **not** the Verified Caller ID (`+916305101934`) — that field only permits
    trial calls/SMS to *reach* it, it can't originate a call.
  - Needs exactly one of `Url` (Twilio fetches TwiML instructions from your server) or
    `Twiml` (inline instructions) — this is the hook point for Zoiko's own call-routing
    logic once Stage 3 starts: our backend will need to serve TwiML dynamically based on
    business-hours rules, forwarding config, etc. (per the arch doc's Voice Routing
    service responsibilities).

## Key implication for Stage 3 sequencing

Stage 3 can't be meaningfully tested end-to-end (a real outbound call) until Stage 2
actually produces an `Active` owned number. Worth keeping in mind when whoever picks up
Stage 3 estimates their own "prove it works" milestone — it's blocked on Stage 2's
purchase flow being real, not just this exploration script.
