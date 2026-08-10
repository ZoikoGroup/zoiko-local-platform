FROM python:3.10-slim

WORKDIR /app

# psycopg2-binary ships its own libpq, so no build-essential needed here -
# curl is just for the container healthcheck below.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# --retries/--timeout: the dependency set backtracks across many candidate
# versions during resolution (some transitive package's newer releases need
# Python >=3.11, which this 3.10 image doesn't have), so the install makes a
# lot of sequential PyPI requests - a single flaky one otherwise kills the
# whole command instead of pip just retrying it.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --retries 10 --timeout 30 -r requirements.txt

# alembic.ini and alembic/ live inside this directory alongside app/, so a
# plain COPY . . picks up everything needed to both serve the API and run
# migrations from this same image (e.g. `docker run <image> alembic upgrade head`).
COPY . .

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Real finding from backend/loadtest.py: a single uvicorn process has a hard
# concurrency ceiling well under 30 in-flight requests - p50/p90/p99 latency
# on plain list endpoints degraded sharply (30ms -> 100-250ms p50, 60ms ->
# 500-1000ms p90) between 10 and 30 concurrent users, while Postgres itself
# stayed near-idle (<2% CPU) the whole time. Not a DB problem - a single-
# process request-concurrency problem. WEB_CONCURRENCY defaults to 4, a
# standard "I/O-bound app on a small shared vCPU" starting point (fly.toml's
# `cpus = 1`) - each worker mostly waits on DB/provider I/O rather than
# computing, so multiple worker processes help even on one shared core.
# Override via `fly secrets set WEB_CONCURRENCY=N` if the machine is resized.
ENV WEB_CONCURRENCY=4
# Runs migrations on every container start rather than relying on a
# platform's separate pre-deploy/release-phase step - that step is a paid
# feature on some platforms (e.g. Render's free tier silently skips it
# with no error), so this is the one approach guaranteed to actually run
# regardless of plan/platform. Fine at numInstances=1 (current deploy) -
# there's no distributed locking here, so if this ever scales to multiple
# instances deploying concurrently, two containers could race the same
# migration. Move to a real release-phase step before scaling past one
# instance.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_CONCURRENCY}"]
