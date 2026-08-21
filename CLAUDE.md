# Zoiko Local — Project Guide

## What this is
Cross-border local numbers + calling + video platform (Skype Number successor).
Source architecture: `docs/Zoiko_Local_Backend_Architecture.docx`, `docs/Zoiko_Local_Phase_1_Engineering_Build_Roadmap.docx`.

## Stack
- Backend: Python + FastAPI + SQLAlchemy + Alembic
- Database: PostgreSQL — Neon (see `.env`'s `DATABASE_URL`), not the local docker-compose instance
- Telecom provider: Twilio (trial account) — wrapped in `backend/app/integrations/telecom/`
- Video provider: LiveKit Cloud — wrapped in `backend/app/integrations/video/`
- Transcription + LLM summarization: Groq (Whisper + Llama) — wrapped in
  `backend/app/integrations/transcription/` and `backend/app/integrations/llm/`
- Frontend: Next.js (TypeScript) — added in Stage 9, not yet started

## Rules — don't break these
- Only two folders hold application code: `backend/` and `frontend/`. Don't create new top-level folders for things that belong inside one of them (e.g. AI code lives in `backend/app/intelligence/`, provider integrations live in `backend/app/integrations/`) — `docs/`, `infrastructure/`, `scripts/`, `.github/` are the only things allowed to stay at root, since they aren't backend- or frontend-specific.
- Only files inside `backend/app/integrations/<category>/` may import a vendor SDK directly (Twilio, Stripe, etc.). Everything else calls functions from there. This is the "Provider Gateway" pattern — keeps vendor swaps cheap later.
- Every table needs a UUID primary key + `created_at`.
- Compliance rules are stored as data (a table), never hardcoded `if` statements.
- Log every state-changing action through `backend/app/audit/service.py`'s `log_event()` — don't skip audit logging to save time.
- After changing a SQLAlchemy model, create an Alembic migration — never hand-write `ALTER TABLE`.

## Build sequence (see README.md for full list)
We build one stage fully working end-to-end before starting the next. Do not scaffold folders for a stage you haven't reached yet.

**Exception (2026-07-30):** Founder approved starting Stage 2/3 telecom groundwork
(`integrations/telecom/twilio.py`, `app/media/voice.py`) in parallel with Stage 1, since
it has no dependency on Account/User models. This code has no auth, entitlement, or audit
checks yet and is not wired to any data model — it's a working Provider Gateway + call
webhook, not a finished feature. Whoever finishes Stage 1 and starts Stage 2 properly
needs to go back and add the Account/Number linkage, audit logging, and access control
before this is production-ready. Don't treat its existence as "Stage 3 is done."

## What NOT to build yet
No Kubernetes, no multi-country compliance, no ZoikoNex integration (billing/Stripe work
is paused — connection model to the real ZoikoNex system needs clarifying before building
a stand-in that might get thrown away).

**Exception (2026-08-04):** The "no Kafka/event bus" deferral above is lifted — founder
directed building it. Single-node Apache Kafka (KRaft mode, no Zookeeper) runs via
`docker-compose.yml` (host port 9095, not 9092 — an unrelated project's broker already
uses 9092 on this dev machine). Producer/consumer wrapped in
`backend/app/integrations/eventbus/kafka.py` (Provider Gateway); domain-facing publish
functions in `backend/app/events/service.py`. Publishing is best-effort — a Kafka outage
never fails the underlying business transaction, same rationale as the SMS/push
notification fan-outs. Wired into representative real call sites, not every domain event
in the system: `number.reserved`/`number.activated`/`number.suspended`
(`numbering/numbers/service.py`), `call.started`/`call.ended` (`media/service.py`), and
`notification.sent` (`notifications/service.py`). Extending the same pattern to other
domains (video, receptionist, porting, compliance, etc.) is straightforward but not yet
done — same scoping decision as the notification template registry (~9 core templates,
not the full 195+39 estate). Single-broker dev gotcha worth remembering: the default
`offsets.topic.replication.factor` is 3, which can never be satisfied with 1 broker and
silently hangs every consumer group's partition assignment forever (producers still work
fine, since they don't need the `__consumer_offsets` coordination topic) — the
docker-compose service pins it to 1.

**Exception (2026-07-31):** The video/AI Receptionist deferral below is lifted.
Stage 2 (numbers: search/reserve/purchase), Stage 3 (voice: inbound/outbound calling,
signature-verified webhooks, call records), and Stage 5 (voicemail) are now built,
tested, and wired to auth + audit — the "core number+voice loop" the original
deferral was gating on. Founder directed moving on to video calling and AI call/voicemail
summaries next.

Built and tested end-to-end against real providers (not stubs): 1:1 video rooms via
LiveKit (`backend/app/media/video.py`, `POST/GET /media/video/rooms*`), and AI voicemail
summaries via Groq Whisper + Llama (`backend/app/intelligence/`, `POST
/intelligence/voicemails/{id}/summarize`).

**2026-08-01:** Forwarded inbound calls (the two-way conversation path — see
`should_forward_call` in `backend/app/media/service.py`) are now recorded end-to-end via
Twilio's `<Dial record="record-from-answer-dual">`, landing on `call_records.recording_url`
via the new `/media/voice/recording-callback` webhook. AI call summaries reuse the same
Groq Whisper + Llama pipeline as voicemail (`POST /intelligence/calls/{id}/summarize`),
gated on the same AI-processing consent record. Not recorded: outbound calls (the
`/media/voice/outbound` demo endpoint is a one-way canned `<Say>`, not a conversation) and
calls handled by the AI Receptionist or plain voicemail branches (those already have their
own dedicated capture — a receptionist transcript or a voicemail recording — so recording
the outer call too would just duplicate audio already on file). Full AI Receptionist
(answering, routing, escalation per the roadmap doc) **has since been built — see the
2026-08-20 entry below.** This paragraph is left as-is for history; don't trust the last
sentence of it.

**Exception (2026-08-06):** Three follow-up completions, all backend-only, no frontend changes.

*Kafka event coverage extended.* `backend/app/events/service.py` now also publishes
`voicemail.created` (`media/service.py:record_voicemail`), `transcript.completed` +
`ai.summary.completed` (`intelligence/service.py:_analyze_and_store`), `usage.rated`
(`usage/service.py:record_usage_event`), and `compliance.case_required`/`case_approved`/
`case_rejected` (`compliance/service.py`) — closing most of the gap against the
Architecture doc's §8 event table. `compliance.case_expired` is still not published: there
is no expiry-checking job anywhere in the codebase (only an `expires_at` column with
nothing that reads it), so there was no real call site to wire — that's a new feature
(a retention-style purge/expiry sweep), not event wiring, and is out of scope here.
`backend/app/events/consumer.py`'s `TOPICS` list was extended to match (`zoiko.voicemail`,
`zoiko.intelligence`, `zoiko.usage`, `zoiko.compliance`).

*Fraud/Risk: account scoring + auto-suspend.* `backend/app/risk/models.py` adds a
`RiskSignal` table (migration `a80b7b11ce8e`) — every time `assert_destination_allowed`/
`assert_outbound_velocity_ok` blocks a call, it now records a weighted signal (audited)
instead of leaving no trace beyond a rejected request. `compute_account_risk_score`
(`backend/app/risk/service.py`) sums weighted signals over a trailing 24h window
(conservative first-pass weights/threshold, same caveat as the pre-existing velocity
limit); crossing `AUTO_SUSPEND_THRESHOLD` calls a new
`suspend_numbers_for_account_by_system` (`numbering/numbers/service.py`) that suspends
every active number on the account with no `User` in the loop (a system actor, not a
customer/staff one). Staff can inspect via `GET /risk/accounts/{id}/score` and reverse via
`POST /risk/accounts/{id}/reinstate` (new `reactivate_numbers_for_account_by_staff`,
SUPER_ADMIN/COMPLIANCE_OFFICER only). Payment risk from the Architecture doc's §5 "Fraud
and Risk" is still not built — there's no billing/payment system old enough to have
payment-risk history yet. **Device fingerprinting has since been built — see the
2026-08-20 entry below; don't trust the "not built" claim above for it.**

*Provider Gateway secondaries are now real vendor clients, not stubs.* Every
`_secondary_stub.py` (telecom→Vonage, video→Daily.co, llm→OpenAI, transcription→Deepgram,
kyc→Sumsub, storage→a second S3-compatible bucket e.g. Backblaze B2, email→SendGrid,
webpush→OneSignal) now makes a real, correctly-shaped API call instead of unconditionally
raising. **None of these are tested against a live account** — no real secondary-vendor
credentials exist yet, same situation the primaries were in before Twilio/LiveKit/Groq/
Stripe/Resend got real keys. Each `*_FAILOVER_ENABLED` flag still defaults `false`; flip it
only after filling in the matching `*_secondary_stub.py` module's credentials in `.env`
(new blank vars added there, grouped under "Multi-provider failover secondaries"). A few
functions genuinely can't fail over given how the domain models are built today — e.g.
Vonage can't act on a Twilio-issued call/recording SID or interpret Twilio TwiML as its
own NCCO call-control format — those raise a clearly-labeled error explaining the
architectural gap rather than pretending compatibility; seeing that error means the primary
is down AND the operation is one of the ones without a real cross-vendor equivalent, not a
misconfiguration.

Not run: the backend test suite. This sandbox has no network route to the Neon Postgres
in `DATABASE_URL` (`alembic current` and pytest's session-scoped schema fixture both need a
live DB connection), so none of the above was verified beyond `python -m py_compile`/import
checks and building the full FastAPI app object. Run `pytest` from an environment with real
DB access before trusting this beyond "it imports."

**Correction (2026-08-19):** Two claims above are now stale, confirmed built (not something
built today — just never reflected here):

*AI Receptionist is real*, not summarization-only as the 2026-08-01 entry says. Real
answering (`POST /media/receptionist/respond`, `backend/app/media/receptionist.py`), caller
qualification via Groq extraction, urgency-based escalation to a nominated team member
(`escalation_user_id`), staff assignment/summary editing, and a deterministic guardrail
scanner (`backend/app/intelligence/guardrails.py`) blocking pricing/legal/medical
commitments — matching the roadmap doc's spec.

*Device fingerprinting is real end-to-end*, not backend-only-and-waiting-on-frontend as the
2026-08-06 entry says. Frontend sends a coarse, no-third-party-SDK fingerprint as an
optional header on signup (`computeDeviceFingerprint()`, `frontend/src/lib/api.ts`);
backend detection lives in `backend/app/risk/service.py`
(`record_fingerprint_sighting`/`is_suspected_fingerprint_abuse`/`check_fingerprint_on_signup`).
Payment risk is still genuinely not built (still no billing/payment system for it to hook
into).

Also: the Vonage `_secondary_stub.py` entry above ("none of these are tested against a live
account") is stale for Vonage specifically — real Vonage credentials were added and
live-tested this session (`search_available_numbers`/`buy_number` against a real account;
found and fixed a real bug in the process, `mobile` → `mobile-lvn` number-type mapping).
`TELECOM_FAILOVER_ENABLED=true` in `.env`. The other five secondaries (Daily.co/OpenAI/
Deepgram/Sumsub/Backblaze B2/SendGrid/OneSignal) remain untested against live accounts, as
originally stated.

**Decision (2026-08-20): video calling stays in Zoiko Local.** The Commercial Billing
Operating Standard doc (§1/§G1/§35) flags a "Critical" consistency conflict against the
Phase 1 Roadmap doc: the newer Billing Standard says video should be a governed Zoiko Sema
integration/handoff, not a separately-rated Zoiko Local product, while the Roadmap doc (the
doc this build actually followed) says video launches in Phase 1 as a real Zoiko Local
feature. Product owner confirmed directly: keep the existing, fully real, tested LiveKit
video integration (`backend/app/media/video.py`, its own `Plan.monthly_video_minutes`/
`Plan.max_video_participants` entitlements, and its Kafka event coverage) as a genuine
Zoiko Local product. Not a code change — video already worked exactly this way; this
resolves the doc conflict itself so it stops being re-flagged as open in future audits.
If the Zoiko Sema boundary is ever revisited, that's a new decision to make explicitly,
not a reversion to an old unresolved one.

**Decision (2026-08-20): Zoiko Local price book formally approved.** All 8 real
(`is_placeholder=False`) `price_catalog_entries` rows for `price_book_version`
`2026-08-14-global-launch-usd` (Starter/Business/Pro/Scale × monthly/annual — the doc's own
$12.99/$19.99/$29.99/$44.99 and $129/$199/$299/$449 figures) moved PROPOSED → APPROVED →
ACTIVE via `approve_price_catalog_entry`/`activate_price_catalog_entry`, with real
`approval_evidence`/`approved_by`/`approved_at` recorded under the product owner's direct
authorization (not engineering self-ratifying its own placeholder load, per the Readiness
Standard doc's Rule of Authority — see the 2026-08-19 revert of this same price book back to
PROPOSED, which this entry now formally supersedes). `run_billing_cycle`'s own
status/is_placeholder gate will now treat these as genuinely chargeable outside development.
Fixed a real bug found while doing this: `activate_price_catalog_entry`'s "retire whatever
was previously ACTIVE" query didn't scope by `billing_period` (added after that function was
originally written) — activating a plan's ANNUAL entry would have silently retired that same
plan's already-ACTIVE MONTHLY entry, and vice versa, purely because they share the same
plan_code+market. Fixed and verified live: all 8 rows are correctly ACTIVE simultaneously.

**Merge note (2026-08-21):** the anilupdated branch's note below (from a separate, parallel
session) says annual pricing and the AI Receptionist add-on price are still placeholder -
that was true when it was written, but both are now built (see the two Decision entries
above and the BillingPeriod/AI-add-on work in this same commit). It also loaded real prices
under `catalog_version 2026-launch-001`, a different version string than the
`2026-08-14-global-launch-usd` one approved above - reconciled in billing/service.py so only
one version's rows are ACTIVE per plan/market/period; see that file's merge resolution for
which one won and why.

**Exception (2026-08-20):** This doc had drifted stale on two features that were actually
finished a while ago — a full audit against the architecture/roadmap docs turned this up,
so correcting it here instead of leaving the false claims above uncorrected.

*AI Receptionist is fully built, not summarization-only.* `backend/app/media/receptionist.py`
implements the live-call TwiML flow (business-hours answering, caller qualification via
`extract_receptionist_qualification`, one bounded follow-up turn, DTMF callback-time
selection, urgency-based escalation/human-handoff, and guardrails against
legal/financial/medical/pricing commitments in `intelligence/guardrails.py`), wired into
inbound routing at `media/voice.py` (`elif owner.ai_receptionist_enabled:`). Per-number
config lives on `PhoneNumber.ai_receptionist_enabled`/`escalation_user_id`/
`forwarding_number`. This shipped in the same work that added multi-turn/booking support;
the "still not built" line above is wrong and predates it.

*Device fingerprinting is built.* `RiskSignalType.DEVICE_FINGERPRINT_ABUSE` plus
`check_fingerprint_on_signup`/`_on_login`/`_on_call` (`risk/service.py`) record a
`DeviceFingerprintSighting` off an optional `X-Device-Fingerprint` header sent by the
frontend already, and raise a `RiskSignal` once one fingerprint touches ≥4 distinct
accounts in 24h — detection-only by design, never blocks the request (shared
devices/networks would cause false positives). Fully covered in `test_risk.py`.

*AI Receptionist call minutes are now metered.* `ReceptionistCall` gained a
`duration_seconds` column (migration `9b4e2f7a1c63`); `update_call_status` (`media/service.py`) populates it from the same Twilio
`CallDuration` used for `call_seconds`, and records a parallel `ai_receptionist_minutes`
usage event (`usage/service.py`'s existing `record_usage_event`, same idempotency-key
pattern as `call_seconds`/`video_participant_minutes`). This only meters usage — it does
not yet enforce or bill the Pricing doc's included-allowance/overage rule (100 min on the
$29 workspace add-on, 50/150 min on Pro/Scale, $0.39/min overage); `get_usage_summary`
reports `ai_receptionist_minutes` the same "informational only" way it reports every other
metered resource today. Building actual overage billing is a separate, larger piece of
work than metering the raw usage — deliberately not done in the same pass.

*Kafka event coverage extended again.* Added `audit.event.recorded` (published from
`audit/service.py`'s `log_event()` — audit logging was Postgres-only before this),
`number.purchase_confirmed` (numbering purchase completion), and named
`payment.failed`/`payment.restored` events in billing (billing previously only published a
generic `subscription.payment_event` with a status field; that publish call is left in
place, these are additive). `video.session.started`/`video.session.ended` were added
alongside the existing `video.room.created`/`video.room.ended` (also left in place, since
some consumer logic may already key on the old names) — closes the gap against the
Architecture doc's §8 event table except where noted otherwise.

*The two existing expiry/purge sweeps are now actually scheduled.* `compliance.service.
expire_overdue_cases()` and `retention.service.purge_expired_recordings()` existed but had
no scheduler — both now run from `backend/app/ops/scheduled_reconciliation.py`, the same
script `render.yaml`'s (currently-dropped, free-tier-unavailable) daily cron comment
already pointed at. No new scheduling infrastructure was introduced; this just uses the one
daily job slot that already existed for ZoikoNex reconciliation and number-renewal
listing.

*Real commercial prices loaded into the price catalog.* The Starter/Business/Pro/Scale
figures from the "Global Plans, Pricing & Commercial Launch Standard" doc
($12.99/$19.99/$29.99/$44.99 monthly) are now seeded as real, `ACTIVE`, non-placeholder
`PriceCatalogEntry` rows (migration `c4a891fe6d27`, catalog_version `2026-launch-001`) —
`run_billing_cycle` will actually charge these once real Stripe billing runs outside
`development`. The `pro`/`scale` `Plan` rows themselves already existed from an earlier,
separate migration (`4ebb299b8b5f`, 2026-08-14) that this session hadn't seen before
starting this work — only the missing price-activation half needed building. Annual
pricing (~17% off, billed upfront) is NOT modeled: `Subscription` has no billing-interval
concept beyond one `current_period_start/end` pair, so only the monthly figures are
active; adding a second interval is a bigger change than seeding a price. **Still
placeholder/unimplemented:**
the AI Receptionist add-on's own $29/mo price row, per-number-type/country `NumberRate`s
(numbers still purchase at the old flat $1 test price), and a real PSTN
origin×destination×direction×number-type rate card (`CallingRate` is still one flat
cents-per-minute number keyed only by *origin* country — the Pricing doc's explicit
recommended US figures were not back-filled into it in this pass because the model has
nowhere to put a direction, and reshaping that table is a bigger schema change than seeding
prices into a model that already supported it).

*Uncaptured ZoikoNex payments now enter the reconciliation queue.* The
`capture_payment_intent` failure itself (`integrations/billing/zoikonex.py`) is explicitly
documented in its own code as broken on *ZoikoNex's* side (evidence-ledger gRPC marshaling)
— that's a defect in a system this repo doesn't own the code for, so nothing here can "fix"
the bug itself, and `run_billing_cycle`'s existing tolerate-and-return-`captured: False`
behavior for that failure was left as-is. What was missing: that outcome never showed up
anywhere staff could see it beyond hand-querying `ZoikoNexSyncEvent` payloads.
`run_zoikonex_reconciliation` (migration `d1f7a3e9c052`) now adds a fourth leg -
`PAYMENT_AUTHORISED_NOT_CAPTURED` - that scans for `PAYMENT_COLLECTED` sync events with
`captured: false` and opens a real `ZoikoNexReconciliationException` for each one, same
operations-queue treatment every other kind of drift already got. `ZoikoNexReconciliationRun`
gained a matching `uncaptured_payments_found` counter.

**Not touched in this pass, and why:** the 234-family email template estate (currently
~100 seeded / ~48 wired) was left alone — writing plausible-sounding copy for ~150
templates nobody has approved isn't a "fix," it's inventing customer-facing legal/
compliance/billing language that should come from Product or Legal, not be guessed here.
