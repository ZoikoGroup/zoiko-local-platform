# Zoiko Local — Project & Integrations Reference (for GCP deployment)

We're moving deployment from Render (backend) + Vercel (frontend) to a GCP
VM. This doc isn't a step-by-step setup script — you know how to stand up a
VM, systemd service, and reverse proxy. This is the project map: what it is,
what it depends on, and every third-party service it talks to, so nothing
gets missed when wiring it up on GCP.

---

## What this project is

Cross-border local numbers + calling + video platform (Skype Number
successor). Customers buy local phone numbers in different countries, make/
receive calls and SMS, run video calls, and get AI-generated call/voicemail
summaries and an AI receptionist. There's a customer dashboard and a
separate internal staff/ops console.

---

## Components

| Component | Stack | Currently runs as | Talks to |
|---|---|---|---|
| `backend/` | Python, FastAPI, SQLAlchemy, Alembic | Docker container on Render → will be a `systemd` service on GCP | Postgres (Neon), all third-party APIs below |
| `frontend/` | Next.js 16 (TypeScript) | Vercel → will be `pm2`-managed on GCP | Backend API only |

Only these two folders hold app code — everything backend-related lives
under `backend/app/`, nothing else at repo root runs as a service.

**Database:** PostgreSQL, hosted on **Neon** (not moving — stays external to
whatever compute the app runs on, GCP included). Migrations via Alembic;
`alembic upgrade head` runs on every deploy.

**Vendor SDK rule (matters for GCP wiring too):** every third-party call
goes through `backend/app/integrations/<category>/` — the "Provider
Gateway" pattern. If you're checking what talks to a given vendor, that
folder is always the answer; nothing outside it imports a vendor SDK
directly.

---

## Third-party integrations

Every one of these needs its own credentials set as environment variables
on the GCP VM (see `render.yaml` in the repo root for the exact variable
names — it's the authoritative list already used in production).

| Service | What it's for | Wired in |
|---|---|---|
| **Twilio** | Primary telecom — number search/purchase, inbound/outbound calling, SMS, call recording | `backend/app/integrations/telecom/twilio.py` |
| **Vonage** | Secondary/failover telecom — kicks in when Twilio has no coverage for a country (e.g. India) or is down. Live-tested for number search; number *purchase* via Vonage is currently blocked by a Vonage-side 401 on this account (external issue, not code) | `backend/app/integrations/telecom/_secondary_stub.py` |
| **LiveKit Cloud** | Video calling (1:1 rooms) | `backend/app/integrations/video/` |
| **Groq** | Whisper (transcription) + Llama (LLM) — call/voicemail summaries, AI receptionist qualification | `backend/app/integrations/transcription/`, `backend/app/integrations/llm/` |
| **Stripe — Payments** | Checkout, subscriptions, plan upgrades/downgrades, mid-cycle proration | `backend/app/integrations/billing/stripe_checkout.py` — separate credentials from Stripe Identity below, don't mix them up |
| **Stripe — Identity** | KYC/identity verification — a different Stripe product/scope, its own secret key + webhook secret | KYC integration module, separate from Payments |
| **S3-compatible storage (Backblaze B2)** | Call recordings, voicemail audio, PDFs, other file storage | `backend/app/integrations/storage/` |
| **Resend** | Transactional email — signup, billing, alerts. Needs a verified custom domain (zoikolocal.com) to send to real customers; falls back to a sandbox sender otherwise | `backend/app/integrations/email/` |
| **Web Push (VAPID)** | Browser push notifications | notification service — `VAPID_PUBLIC_KEY`/`VAPID_PRIVATE_KEY` on the backend, the public key also baked into the **frontend** build |
| **Cohere** | Semantic search / embeddings | LLM/search module |
| **Kafka** (self-hosted, single-node KRaft) | Internal event bus — number/call/voicemail/usage/compliance events. Best-effort; a Kafka outage never blocks the actual business transaction | `backend/app/integrations/eventbus/kafka.py`, published from `backend/app/events/service.py` |
| **Google Sign-In** | OAuth login | `NEXT_PUBLIC_GOOGLE_CLIENT_ID` (frontend, build-time) + backend token verification |
| **ZoikoNex** | Separate internal billing/commerce system — real, tested client, but 2 known bugs on ZoikoNex's own side (payment capture, bill-cycle close) still block full billing through it. Proration is intentionally built to bypass ZoikoNex and go straight through Stripe instead | `backend/app/integrations/billing/zoikonex.py` |

**Secondary/failover providers** (Daily.co, OpenAI, Deepgram, Sumsub, a
second S3 bucket, SendGrid, OneSignal) exist as real API clients behind
`*_FAILOVER_ENABLED` flags, all defaulting off — untested against live
accounts, except Vonage. Leave them off unless deliberately setting one up.

---

## Frontend-specific dependencies

Three env vars get compiled into the JS bundle at `next build` time (not
read at runtime) — must be set correctly *before* building, every time:
`NEXT_PUBLIC_API_BASE_URL`, `NEXT_PUBLIC_GOOGLE_CLIENT_ID`,
`NEXT_PUBLIC_VAPID_PUBLIC_KEY`.

Next.js version is 16.2.12 — newer than what most tooling/docs assume;
`frontend/AGENTS.md` has a note about this if anything behaves unexpectedly.

---

## What already exists for GCP specifically

`.github/workflows/deploy.yml` already has an SSH-based deploy job:
backend → `/var/www/zoiko-local-backend/app` as a systemd service
(`zoiko-local-backend`), frontend → `/var/www/zoiko-local-platform/app` via
pm2 (`zoiko-local-platform`). Needs 3 repo secrets:
`GCP_SSH_HOST`/`GCP_SSH_USER`/`GCP_SSH_KEY`. Currently
`workflow_dispatch`-only (manual trigger) rather than auto-on-push, since no
GCP VM has been confirmed live yet — flip it back to automatic once the VM
is set up and verified.

`backend/Dockerfile` is the other source of truth for how the backend
actually boots: `alembic upgrade head && uvicorn app.main:app --workers
${WEB_CONCURRENCY}` (default 4 workers), non-root user, needs `ffmpeg`
installed (audio extraction for transcription) alongside Python deps.

**Cutover note:** Neon DB is shared and unaffected either way, so GCP can be
verified side-by-side with the still-live Render/Vercel setup before DNS
actually moves — just remember to repoint the Twilio/Stripe(x2)/Resend/
LiveKit webhooks at the new backend URL as part of cutover, not after.
