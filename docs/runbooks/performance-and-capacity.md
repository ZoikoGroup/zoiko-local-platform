# Runbook: The App Feels Slow, or Is Erroring Under Load

## Real finding this session (read this first)

Load testing (`backend/loadtest.py`) found the production Dockerfile ran a
**single uvicorn process with no `--workers` flag at all**. p50/p90/p99
latency on plain list endpoints degraded sharply between 10 and 30
concurrent users (30ms → 100-250ms p50, 60ms → 500-1000ms p90), while
Postgres itself stayed under 2% CPU and mostly idle the whole time — **it
was never a database problem**, it was a single-process request-concurrency
ceiling. Fixed by adding `WEB_CONCURRENCY` worker processes to the
Dockerfile (default 4).

**So: if the app is slow under load, check worker count and CPU before
touching the database.**

## Diagnose

1. **Is it actually load, or a single stuck request?** Check
   `GET /ops/errors/summary?hours=1` for a spike, and structured request
   logs (`fly logs`) for `duration_ms` outliers on specific paths vs. a
   uniform slowdown across everything.
2. **Uniform slowdown across all endpoints** → likely a concurrency
   ceiling (see below) or the DB — check
   [database-outage.md](database-outage.md)'s connection-count query.
3. **Slowdown scoped to one feature area** (e.g. only `/media/video/*`) →
   check [provider-outage.md](provider-outage.md) instead — a vendor
   degrading (not fully down) can look like a performance problem rather
   than an outage.
4. **Machine-level CPU/memory** — `fly status`/`fly logs` or your
   platform's metrics. `backend/fly.toml` currently runs `cpus = 1`
   (shared), `memory = "512mb"` — a genuinely CPU-bound workload (not just
   I/O-wait, which is what the worker-count fix targets) will need a bigger
   machine, not more workers.

## Levers, in the order load testing actually validated them

1. **`WEB_CONCURRENCY`** (Dockerfile env, default 4) — the primary fix
   found this session. On a single shared vCPU (`fly.toml`'s
   `cpus = 1`), more worker processes still help because each one mostly
   waits on DB/provider I/O rather than computing — but there's a point of
   diminishing/negative returns from process-switching overhead once you
   exceed what the actual CPU can schedule. Adjust via:
   ```bash
   fly secrets set WEB_CONCURRENCY=6
   ```
   The Dockerfile's `ENV WEB_CONCURRENCY=4` is only a default — `fly secrets
   set` injects a real runtime environment variable that overrides it, and
   Fly restarts the machine to apply it. No rebuild/redeploy needed.
2. **`DB_POOL_SIZE` / `DB_MAX_OVERFLOW`** — only relevant if you've
   confirmed (via `pg_stat_activity`) that requests are actually queueing
   for a DB connection, which load testing did NOT find to be the case at
   the ceiling this app has hit so far. See
   [database-outage.md](database-outage.md).
3. **Machine size** (`backend/fly.toml`'s `[[vm]]` block) — bump `cpus`/
   `memory` if per-request CPU work (not I/O-wait) is the actual
   bottleneck. Not yet needed as of this session's testing.

## Re-running the load test yourself

```bash
cd backend
python loadtest.py --base-url https://<your-fly-app>.fly.dev --users 50 --duration 30
```

Two scenarios run by default: a login-rate-limiter burst check (confirms
the 5/minute limit degrades cleanly under concurrency, not a capacity
test), then N authenticated users hammering the dashboard's read endpoints
for the given duration, reporting p50/p90/p99 latency and error rate per
endpoint. See the script's own docstring for flags.

**A note on trusting the numbers:** this session's testing found that
running the load-generating client on the *same machine* as the server and
database (a local dev setup) introduces enough CPU contention between them
to make absolute latency numbers unreliable for capacity planning — one
comparison run showed *worse* numbers after a config change that later
testing (in a cleaner environment) showed was actually neutral-to-positive.
Always run `loadtest.py` from a separate machine from whatever you're
testing when the numbers need to inform a real capacity decision, not just
a quick sanity check.
