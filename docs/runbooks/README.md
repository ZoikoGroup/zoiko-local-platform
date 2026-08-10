# Support Runbooks

Incident-response guides for Zoiko Local, written from what this codebase
actually does — not generic boilerplate. Each one assumes you have:

- `fly` CLI access to the backend app (`zoiko-local-api` in `backend/fly.toml`,
  region `iad`), or SSH/shell access to wherever the backend is actually
  deployed.
- A staff account (`PlatformStaffRole.SUPER_ADMIN` or similar) to log into
  `/staff/login` on the frontend, for the staff-only diagnostic endpoints
  referenced below.
- Read access to whichever Postgres instance is live (Neon in production;
  see `alembic.ini`/`.env` for the connection string — never paste it into a
  ticket or chat).

## Where to look first, always

1. **`GET /ops/status`** (no auth — same page as `/status` on the frontend)
   — the customer-facing view. If this shows "degraded" on a real component,
   customers are already seeing it.
2. **`GET /ops/provider-status`** (staff auth) — per-vendor `configured`/`ok`
   detail that `/ops/status` deliberately hides from customers. This tells
   you *which* vendor (Twilio, LiveKit, Groq, Stripe Identity, Resend, S3,
   Cohere) is actually the problem.
3. **`GET /ops/errors/summary?hours=1`** (staff auth) — every 5xx response
   and unhandled exception from the last hour, grouped by exception
   type/path/status. This is usually the fastest way to see "is one thing
   failing repeatedly" before you go hunting through logs. See
   [error-monitoring-triage.md](error-monitoring-triage.md).
4. **`fly logs`** (or your platform's log viewer) — structured JSON request
   logs from `app.core.error_logging.ErrorLoggingMiddleware`, one line per
   request: `{"request_id", "method", "path", "status_code", "duration_ms"}`.
   Every error response also carries an `X-Request-ID` response header — if
   a customer reports "it broke," ask for that header's value (browser
   DevTools → Network tab) and grep the logs for it to find the exact
   request.

## Runbook index

| Situation | Guide |
|---|---|
| A third-party vendor (Twilio, LiveKit, Groq, Stripe Identity, Resend, S3, Cohere) is down or erroring | [provider-outage.md](provider-outage.md) |
| Postgres/Neon is unreachable, slow, or out of connections | [database-outage.md](database-outage.md) |
| The app feels slow, or is throwing 500s under load | [performance-and-capacity.md](performance-and-capacity.md) |
| You need to investigate a specific reported error or a spike in 5xx responses | [error-monitoring-triage.md](error-monitoring-triage.md) |

## What's NOT covered here (yet)

- **Formal on-call / paging** — there's no PagerDuty/Opsgenie-equivalent
  wired up. Right now "on-call" means whoever has `fly` access and is
  watching `/ops/status` or answering a customer email. If you set up
  paging later, wire it to `/ops/errors/summary` and/or a log-based alert
  on `status_code >= 500` rate, and update this README.
- **A formal third-party security pen-test.** A manual code-level security
  review happened (see git history: "Fix 3 real security findings from a
  codebase security review") — that is not the same thing as a pen-test,
  and one hasn't been scheduled.
