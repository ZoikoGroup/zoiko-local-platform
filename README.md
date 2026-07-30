# Zoiko Local

AI-native, cross-border local numbers + calling + video platform (Skype Number successor).
See `docs/` for the source architecture and roadmap documents.

## Project layout

```
backend/                     FastAPI backend — the only home for server-side code
  app/
    core/                     config, database, security
    numbering/                identity, teams, numbers, provisioning, entitlements, usage
    media/                    voice, voicemail, video
    intelligence/             AI voicemail summaries, AI Receptionist (added in a later stage)
    compliance/               rules-as-data engine
    billing/                  entitlement <-> billing sync
    notifications/            email / SMS dispatch
    admin/                    ops console API
    audit/                    append-only event log
    integrations/             Provider Gateway — one subfolder per vendor category
      telecom/                  Twilio / Plivo
      billing/                  ZoikoNex (or Stripe as local stand-in)
      notifications/            Email / SMS providers
  tests/                    backend unit/integration tests
  alembic/                  DB migrations

frontend/                   Next.js (TypeScript) web dashboard — not started yet (Stage 9)
  tests/                    frontend tests

docs/             Architecture, API docs, ER diagrams (not backend/frontend-specific, stays at root)
infrastructure/   Docker, Nginx, Kubernetes, Terraform (added once deploying beyond localhost)
scripts/          Deployment & utility scripts
.github/          CI/CD workflows
```

Only two folders hold application code: `backend/` and `frontend/`. Everything else
at the root is supporting material (docs, infra config, scripts, CI) that isn't
specific to either one.

## Build sequence

We are building this stage by stage, not all at once:

1. **Stage 0 — Skeleton** ← you are here. FastAPI app + Postgres connection.
2. Stage 1 — Identity (accounts, auth)
3. Stage 2 — Number Inventory + Twilio integration
4. Stage 3 — Voice Routing (inbound/outbound calling)
5. Stage 4 — Audit logging
6. Stage 5 — Voicemail
7. Stage 6 — Entitlements + Billing (Stripe as ZoikoNex stand-in)
8. Stage 7 — Compliance rules stub
9. Stage 8 — Notifications
10. Stage 9 — Frontend dashboard
11. Stage 10 — Video, AI services, real infra/CI (later phase)

## Local development

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
cd ..
make db        # starts Postgres
make migrate   # applies migrations
make seed      # creates a demo account (optional)
make dev       # starts the API with reload
make test      # runs the test suite
```

**Windows only**: `make` isn't installed by default. Install it once with:
```
winget install ezwinports.make
```
(Restart your terminal afterwards so `make` is on PATH.)

