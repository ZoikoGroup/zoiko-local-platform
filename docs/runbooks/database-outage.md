# Runbook: Database Unreachable or Degraded

Production uses Neon Postgres (not the local docker-compose instance —
see root `CLAUDE.md`). Local dev uses `postgresql+psycopg2://zoiko:zoiko@localhost:5433/zoiko_local`
via docker-compose.

## What it looks like to users

Since `app/main.py` registers a global handler for `sqlalchemy.exc.DBAPIError`
(added this session after finding the gap via load testing), a database
outage or connection failure on ANY route now returns a clean, generic:

```
503 {"detail": "Service temporarily unavailable - please try again shortly."}
```

— not a raw 500, and never leaking connection strings/hostnames/credentials
from the underlying error (this was specifically tested: a `pg_hba`-style
error containing a hostname and "password authentication failed" was
confirmed NOT to appear in the response body). Every one of these also
gets a row in `error_events` (see
[error-monitoring-triage.md](error-monitoring-triage.md)) with
`exception_type` like `OperationalError` or `InterfaceError`.

**A spike in 503s across many/all endpoints simultaneously is the signature
of a DB problem** — a provider outage (see
[provider-outage.md](provider-outage.md)) is scoped to specific feature
areas (numbers, video, etc.), a DB problem is global.

## Diagnose

1. **`GET /health`** — liveness only, does NOT check the DB (deliberately —
   see `app/main.py`; a DB blip shouldn't make the platform kill/restart a
   healthy process).
2. **`GET /health/db`** — actually runs `SELECT 1`. Returns
   `{"status": "error", "database": "unreachable", "detail": "..."}` with
   the real driver error if the DB is down. This endpoint has no auth and
   no data, so its detail field is safe to check without staff credentials.
3. **Neon dashboard** (production) — check for an active incident, or a
   compute suspend/cold-start delay if the project is on autosuspend.
4. **Connection count**, if you have direct DB access:
   ```sql
   SELECT count(*), state FROM pg_stat_activity GROUP BY state;
   ```
   A count near/at Postgres's `max_connections` (Neon's pooled endpoint has
   a much higher effective ceiling than a direct connection; check which
   one `DATABASE_URL` points at) alongside many `idle` connections usually
   means a connection leak somewhere, not the DB itself being down.

## Real, load-tested finding: connection pool sizing

`app/core/database.py`'s SQLAlchemy engine pool size is controlled by
`DB_POOL_SIZE` (default 10) and `DB_MAX_OVERFLOW` (default 10) — **per
worker process**. The backend Dockerfile runs `WEB_CONCURRENCY` (default 4)
worker processes, so total possible connections from one machine is
`WEB_CONCURRENCY * (DB_POOL_SIZE + DB_MAX_OVERFLOW)` — 80 by default.

This was deliberately kept conservative after load testing found that
**Postgres itself was never the actual bottleneck** (confirmed via
`pg_stat_activity` showing 1-2 active queries and <2% container CPU under
50 concurrent users) — the real ceiling was request concurrency in a
single-process deployment, fixed by adding `WEB_CONCURRENCY`. Don't
increase the DB pool size as a first response to "the app feels slow" —
check [performance-and-capacity.md](performance-and-capacity.md) first.

If you DO need to raise it (e.g. running more machines against one DB
tier), account for Neon's own connection ceiling for the compute size
you're on, and leave headroom — a real "too many clients already" was
reproduced once this session by stacking multiple uncoordinated processes
against local Postgres's default `max_connections = 100`.

```bash
fly secrets set DB_POOL_SIZE=15 DB_MAX_OVERFLOW=15
```

## Recovery

Nothing needs to be manually replayed — `pool_pre_ping=True` means the
engine discards stale connections and reconnects automatically once the DB
is reachable again. No app restart needed for a transient DB blip.

## Backup & restore (self-hosted/local Postgres)

Production Readiness & Go-Live Decision Standard's reliability gate (A11)
asks for "backups and restore test," not just "a backup runs somewhere" -
this section covers the self-hosted docker-compose Postgres this repo
actually runs today (local dev, and any self-hosted deployment target).
**Neon** (the intended production target per this doc's own opening line)
has its own managed backup/point-in-time-restore and branching mechanism -
use that directly once a real Neon project is provisioned; the scripts
below are for the docker-compose instance specifically.

`scripts/backup_db.sh [output_dir]` - runs `pg_dump` inside the running
`zoiko_local-postgres-1` container (via `docker exec`, so no client tools
need to be installed on the host) and writes a timestamped custom-format
dump to `output_dir` (default `./backups`).

`scripts/restore_db.sh <backup_file> <target_db_name>` - restores a dump
into `target_db_name`, creating it first if needed. **Always requires an
explicit target name** - it will never restore over the live database by
default, specifically so a restore can't become its own incident. To
verify a backup is actually good without touching real data, restore it
into a disposable name (e.g. `zoiko_local_restore_test`) and drop that
database once you've checked it.

**Actually tested end to end** (2026-08-13, not just written and assumed
to work): backed up the live local dev database, restored it into a
scratch `zoiko_local_restore_test` database, confirmed matching row
counts across several tables (`accounts`, `phone_numbers`, `plans`), then
dropped the scratch database. Real finding from that test run: this
container's host has a newer glibc collation version than the database
was initialized under, which makes plain `createdb` fail outright
("template database template1 has a collation version mismatch") -
`restore_db.sh` works around this by creating the target from
`template0` instead of the (broken) default `template1`. If you ever see
that same collation warning elsewhere against this container, this is
why, and the same `--template=template0` workaround applies.

```bash
./scripts/backup_db.sh ./backups
./scripts/restore_db.sh ./backups/zoiko_local_20260813T160702Z.dump zoiko_local_restore_test
# verify row counts / spot-check data against the real DB, then:
docker exec zoiko_local-postgres-1 dropdb -U zoiko zoiko_local_restore_test
```
