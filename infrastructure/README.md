Local dev still uses the root docker-compose.yml for Postgres only.

What now exists, verified by actually building and running each image locally:
- `backend/Dockerfile` — python:3.10-slim, serves the API on :8000, `/health`
  and `/health/db` for container healthchecks. Refuses to boot outside
  `ENVIRONMENT=development` if `JWT_SECRET_KEY` is still the repo's
  placeholder, or if `ALLOWED_ORIGINS` contains `*` (see
  `app/core/startup_checks.py`).
- `frontend/Dockerfile` — multi-stage Next.js standalone build, serves on
  :3000. `NEXT_PUBLIC_API_BASE_URL` must be passed as a build arg (it's
  inlined into the client bundle at build time, not read at runtime).
- `.github/workflows/ci.yml` — runs on every push/PR: backend tests against a
  real ephemeral Postgres service container (full migration chain applied
  from scratch, then `alembic check`, then `pytest -m "not live"`), plus a
  frontend typecheck + production build. Tests marked `@pytest.mark.live`
  (Groq/LiveKit-dependent) are deliberately excluded - see `backend/pytest.ini`.

Still not started: a real hosted Postgres (the app currently points at the
local Docker instance only - CLAUDE.md's mention of Neon was aspirational,
not yet wired up), Nginx/reverse proxy, Kubernetes, Terraform, or any actual
deploy target. Also still needed before a real deploy: a real `JWT_SECRET_KEY`
and `ALLOWED_ORIGINS` set on whatever host runs this, and a stable
`PUBLIC_BASE_URL` (currently an ngrok tunnel in dev) for Twilio/LiveKit
webhooks to call back to.
