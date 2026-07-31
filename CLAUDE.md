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
No Kafka/event bus, no Kubernetes, no multi-country compliance, no ZoikoNex integration
(billing/Stripe work is paused — connection model to the real ZoikoNex system needs
clarifying before building a stand-in that might get thrown away).

**Exception (2026-07-31):** The video/AI Receptionist deferral below is lifted.
Stage 2 (numbers: search/reserve/purchase), Stage 3 (voice: inbound/outbound calling,
signature-verified webhooks, call records), and Stage 5 (voicemail) are now built,
tested, and wired to auth + audit — the "core number+voice loop" the original
deferral was gating on. Founder directed moving on to video calling and AI call/voicemail
summaries next.

Built and tested end-to-end against real providers (not stubs): 1:1 video rooms via
LiveKit (`backend/app/media/video.py`, `POST/GET /media/video/rooms*`), and AI voicemail
summaries via Groq Whisper + Llama (`backend/app/intelligence/`, `POST
/intelligence/voicemails/{id}/summarize`). AI call (not just voicemail) summaries are
still out of scope — calls aren't recorded yet, only voicemails, so there's no audio to
transcribe for a live call. Full AI Receptionist (answering, routing, escalation per the
roadmap doc) is still not built — only summarization exists so far.
