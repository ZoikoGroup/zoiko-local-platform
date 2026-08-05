"""
Standalone load-testing tool (Roadmap Month 5 launch-readiness gate: "load/
chaos testing"). Not part of the app, not a pytest test - a script run by
hand against a real running backend to find real bottlenecks under
concurrent traffic. Requires the backend already running (see README/
CLAUDE.md for the uvicorn command) - this never imports app code, only
talks to it over HTTP like a real client would.

Usage:
    python loadtest.py [--base-url http://127.0.0.1:8010] [--users 50] [--duration 30]
"""

import argparse
import asyncio
import statistics
import time
import uuid
from dataclasses import dataclass, field

import httpx


@dataclass
class RequestResult:
    endpoint: str
    status_code: int
    latency_ms: float
    error: str | None = None


@dataclass
class ScenarioReport:
    results: list[RequestResult] = field(default_factory=list)

    def summary(self) -> dict:
        by_endpoint: dict[str, list[RequestResult]] = {}
        for r in self.results:
            by_endpoint.setdefault(r.endpoint, []).append(r)

        out = {}
        for endpoint, rows in by_endpoint.items():
            latencies = sorted(r.latency_ms for r in rows)
            errors = [r for r in rows if r.error or r.status_code >= 500]
            out[endpoint] = {
                "count": len(rows),
                "errors": len(errors),
                "error_rate": round(len(errors) / len(rows) * 100, 1) if rows else 0.0,
                "p50_ms": round(_percentile(latencies, 50), 1),
                "p90_ms": round(_percentile(latencies, 90), 1),
                "p99_ms": round(_percentile(latencies, 99), 1),
                "max_ms": round(latencies[-1], 1) if latencies else 0.0,
                "status_codes": _status_breakdown(rows),
            }
        return out


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * (pct / 100)
    f, c = int(k), min(int(k) + 1, len(sorted_values) - 1)
    if f == c:
        return sorted_values[f]
    return sorted_values[f] + (sorted_values[c] - sorted_values[f]) * (k - f)


def _status_breakdown(rows: list[RequestResult]) -> dict:
    breakdown: dict[int, int] = {}
    for r in rows:
        breakdown[r.status_code] = breakdown.get(r.status_code, 0) + 1
    return dict(sorted(breakdown.items()))


async def _timed(client: httpx.AsyncClient, endpoint: str, method: str, url: str, **kwargs) -> RequestResult:
    start = time.perf_counter()
    try:
        response = await client.request(method, url, **kwargs)
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(endpoint=endpoint, status_code=response.status_code, latency_ms=latency_ms)
    except httpx.HTTPError as e:
        latency_ms = (time.perf_counter() - start) * 1000
        return RequestResult(endpoint=endpoint, status_code=0, latency_ms=latency_ms, error=str(e))


async def _signup(client: httpx.AsyncClient, base_url: str, report: ScenarioReport) -> str | None:
    """One virtual user signing up fresh - the burst-of-new-signups scenario
    (e.g. a marketing push). Deliberately does NOT also call /auth/login:
    that endpoint is rate-limited to 5/minute per IP (see
    app.numbering.identity.routes.login), and every virtual user in this
    script shares one IP (this machine) - a real login burst-test belongs in
    run_login_burst_test below, done deliberately and read on its own, not
    accidentally starving phase 2 of every token it needs."""
    email = f"loadtest-{uuid.uuid4().hex[:12]}@example.com"
    signup_result = await _timed(
        client, "POST /auth/signup", "POST", f"{base_url}/auth/signup",
        json={
            "account_name": "Load Test Co",
            "account_type": "business",
            "email": email,
            "password": "supersecret123",
        },
    )
    report.results.append(signup_result)
    return email if signup_result.status_code == 201 else None


def _seed_login_tokens(users: int) -> list[str]:
    """Generates real, validly-signed access tokens for `users` fresh
    accounts directly through the app's own DB/signing code (never over
    HTTP) - the same account creation and create_access_token() call
    /auth/signup + /auth/login perform, just without going through the
    rate-limited login endpoint. This is what the read-heavy phase (2)
    actually load-tests: authenticated dashboard traffic, not the login
    endpoint itself - see run_login_burst_test for that, done separately
    and deliberately."""
    from app.core.database import SessionLocal
    from app.core.security import create_access_token
    from app.numbering.identity import service as identity_service
    from app.numbering.identity.models import AccountType

    db = SessionLocal()
    tokens = []
    try:
        for _ in range(users):
            email = f"loadtest-seed-{uuid.uuid4().hex[:12]}@example.com"
            user = identity_service.create_account_with_owner(
                db, "Load Test Co", AccountType.BUSINESS, email, "supersecret123"
            )
            tokens.append(create_access_token(subject=user.id, scope="customer"))
    finally:
        db.close()
    return tokens


async def _read_heavy_loop(
    client: httpx.AsyncClient, base_url: str, token: str, deadline: float, report: ScenarioReport
) -> None:
    """Simulates a logged-in user's dashboard traffic - the read endpoints
    every screen hits on load (numbers, calls, receptionist calls,
    notifications), looped back-to-back until the run's deadline."""
    headers = {"Authorization": f"Bearer {token}"}
    endpoints = [
        ("GET /numbers", "/numbers"),
        ("GET /media/voice/calls", "/media/voice/calls"),
        ("GET /media/receptionist/calls", "/media/receptionist/calls"),
        ("GET /notifications/me", "/notifications/me"),
    ]
    while time.monotonic() < deadline:
        for label, path in endpoints:
            result = await _timed(client, label, "GET", f"{base_url}{path}", headers=headers)
            report.results.append(result)
            if time.monotonic() >= deadline:
                return


async def run_login_burst_test(base_url: str, burst_size: int) -> ScenarioReport:
    """Deliberate, isolated check of the login rate limiter under real
    concurrency (Roadmap "load/chaos testing" - a security control is only
    as good as its behavior under load, not just a single manual test).
    burst_size legitimate, DISTINCT users logging in at the same instant
    from one shared IP (the realistic "everyone in one office" case) -
    confirms the limiter degrades cleanly (clean 429s, no 500s, no race
    condition letting extra requests slip through) rather than measuring
    throughput, since 5/minute is an intentional ceiling, not a target."""
    report = ScenarioReport()
    async with httpx.AsyncClient(timeout=15.0) as client:
        emails = []
        for _ in range(burst_size):
            email = await _signup(client, base_url, report)
            if email:
                emails.append(email)

        async def _login(email: str) -> None:
            result = await _timed(
                client, "POST /auth/login [burst]", "POST", f"{base_url}/auth/login",
                json={"email": email, "password": "supersecret123"},
            )
            report.results.append(result)

        await asyncio.gather(*(_login(e) for e in emails))
    return report


async def run_load_test(base_url: str, users: int, duration: int) -> ScenarioReport:
    report = ScenarioReport()
    print(f"[1/2] Seeding {users} authenticated virtual users directly (bypasses the rate-limited login endpoint)...")
    tokens = _seed_login_tokens(users)
    print(f"      {len(tokens)}/{users} accounts + tokens created")

    limits = httpx.Limits(max_connections=users + 10, max_keepalive_connections=users)
    async with httpx.AsyncClient(timeout=15.0, limits=limits) as client:
        print(f"[2/2] Running read-heavy dashboard traffic for {duration}s across {len(tokens)} users...")
        deadline = time.monotonic() + duration
        await asyncio.gather(*(_read_heavy_loop(client, base_url, token, deadline, report) for token in tokens))

    return report


def print_report(report: ScenarioReport) -> None:
    summary = report.summary()
    total_requests = sum(s["count"] for s in summary.values())
    total_errors = sum(s["errors"] for s in summary.values())
    print(f"\n=== Load test report ({total_requests} requests, {total_errors} errors) ===\n")
    header = f"{'endpoint':<32} {'count':>7} {'errs':>6} {'err%':>6} {'p50ms':>8} {'p90ms':>8} {'p99ms':>8} {'maxms':>8}  status_codes"
    print(header)
    print("-" * len(header))
    for endpoint, s in sorted(summary.items()):
        print(
            f"{endpoint:<32} {s['count']:>7} {s['errors']:>6} {s['error_rate']:>5.1f}% "
            f"{s['p50_ms']:>8.1f} {s['p90_ms']:>8.1f} {s['p99_ms']:>8.1f} {s['max_ms']:>8.1f}  {s['status_codes']}"
        )


async def _main(args) -> None:
    if not args.skip_login_burst:
        print(f"=== Scenario A: login rate-limiter under a {args.login_burst} way concurrent burst ===")
        burst_report = await run_login_burst_test(args.base_url, args.login_burst)
        print_report(burst_report)
        print()

    print(f"=== Scenario B: {args.users} authenticated users, {args.duration}s of dashboard read traffic ===")
    read_report = await run_load_test(args.base_url, args.users, args.duration)
    print_report(read_report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--users", type=int, default=50, help="concurrent authenticated users in scenario B")
    parser.add_argument("--duration", type=int, default=30, help="seconds of read-heavy traffic in scenario B")
    parser.add_argument("--login-burst", type=int, default=10, help="concurrent distinct logins in scenario A")
    parser.add_argument("--skip-login-burst", action="store_true")
    args = parser.parse_args()

    asyncio.run(_main(args))
