# Runbook: Triaging Errors via the Self-Hosted Error Log

Zoiko Local doesn't use a third-party APM (Sentry, etc.) — errors are
captured in-house (`app/observability/`, `app/core/error_logging.py`) so
this needs no external account. It captures **every 5xx response**,
whether from a genuinely unhandled exception or one of the app's own
`raise HTTPException(502/503, ...)` calls after a caught provider/DB
failure — 4xx responses (401/403/404/422/429) are never logged here, since
those are normal expected outcomes, not incidents.

## Endpoints (all staff-auth only, `GET /staff/login` first)

| Endpoint | Use for |
|---|---|
| `GET /ops/errors?limit=100` | Most recent errors, newest first. Each row: `request_id`, `method`, `path`, `status_code`, `exception_type` (null for a "handled" 502/503, e.g. a caught provider failure — see below), `exception_message`, `account_id`/`user_id` if the request was authenticated, `created_at`. |
| `GET /ops/errors/{id}` | Same row plus the full Python traceback (`traceback` field) — omitted from the list view since it's large. Only present when `exception_type` is set (a real unhandled exception, not a handled provider/DB error). |
| `GET /ops/errors/summary?hours=24` | Grouped by `(exception_type, path, status_code)` with a count — "is one thing failing repeatedly" at a glance, before reading individual rows. |

## Reading `exception_type: null` vs. a real type

- **`exception_type` is set** (e.g. `RuntimeError`, `OperationalError`) —
  a genuinely unexpected crash. Pull the `traceback` via
  `GET /ops/errors/{id}` and treat it as a real bug to fix, not just an
  operational blip.
- **`exception_type` is null but `status_code` is 502/503** — the code
  already caught a known failure mode and returned a deliberate error
  response (a provider outage, see
  [provider-outage.md](provider-outage.md); or a DB outage, see
  [database-outage.md](database-outage.md)). This is "working as
  designed" from the app's side — the underlying vendor/DB is what needs
  attention, not the app code.

## Correlating a specific user report

Every response — success or error — carries an `X-Request-ID` header. If a
user reports "I got an error," ask them for that header's value (browser
DevTools → Network tab → the failed request → Response Headers), then:

```
GET /ops/errors?limit=500   # find the row with a matching request_id
```

or grep the structured stdout logs (`fly logs`) for the same
`request_id` — every request (not just errors) logs one JSON line with
`request_id`, `method`, `path`, `status_code`, `duration_ms`, so you can
also confirm what a *successful* request's timing looked like for
comparison.

## What this does NOT catch

- **4xx responses** — by design. A wave of 401s might mean an expired
  token rollout bug, but check `/ops/errors` only after ruling out "this
  is just normal traffic" via the structured request logs' status-code
  distribution.
- **Cohere embedding failures** — both call sites degrade silently with no
  5xx at all (see [provider-outage.md](provider-outage.md)'s Cohere
  section) — check `/ops/provider-status` for this one specifically, not
  the error log.
- **SMS/email notification failures** — recorded in the
  `notification_deliveries` table's `status` column (`failed`), not
  `error_events` — these never raise, by design, since a failed
  confirmation email shouldn't block the action that triggered it.
- **Anything that happened before this session** — `error_events` only
  exists from the migration that added it onward; there's no historical
  backfill.

## Housekeeping

`error_events` has no retention/cleanup job yet — it will grow
indefinitely. If it becomes large enough to matter, add a policy similar to
`backend/app/retention/service.py`'s existing purge pattern (age-based
deletion) rather than growing the table forever. Not needed at current
volume.
