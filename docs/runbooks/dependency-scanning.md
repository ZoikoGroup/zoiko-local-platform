# Runbook: Dependency / Security Scanning

Production Readiness & Go-Live Decision Standard §A10 asks for
"dependency/security scanning" as a launch-gate control. Before
2026-08-13 there was no dependency-scanning step anywhere in this repo
(no CI step, no Dependabot config) - this documents what was actually
run, what got fixed, and what's a real, known, deliberately-deferred gap
rather than something silently ignored.

## What runs now

- Backend: `pip-audit -r requirements.txt` in the `backend` CI job.
- Frontend: `npm audit --production` in the `frontend` CI job.

Both are `continue-on-error: true` for now - **informational, not a hard
gate**. See "Why not a hard gate yet" below.

## 2026-08-13 findings and what was done about each

**Backend (pip-audit against the actual pinned requirements.txt, not a
theoretical scan):**

| Package | Was | Now | Action |
|---|---|---|---|
| `python-dotenv` | 1.0.1 | 1.2.2 | Bumped - patch/minor, verified via full test suite, no breakage |
| `python-jose` | 3.3.0 | 3.5.0 | Bumped - fixes PYSEC-2024-232/233, PYSEC-2025-185 (JWT library - checked carefully given it signs every access token in this app); verified via new `tests/test_security.py` plus the full suite |
| `python-multipart` | 0.0.9 | 0.0.32 | Bumped - fixes multiple CVEs; verified via `tests/test_compliance.py` (the one real file-upload endpoint) plus the full suite |
| `starlette` (transitive via `fastapi`) | 0.38.6 | *unchanged* | **Not bumped.** Fixing this needs a `fastapi` major-version bump (0.115 → much later), which is a real compatibility risk across 270+ routes this session didn't have safe time to verify end-to-end. Flagged, not ignored - see "Deliberately deferred" below. |
| `pytest` | 8.3.3 | *unchanged* | Dev-only dependency (never ships to production) - lower priority than a runtime dependency; a pytest 8→9 major bump risks breaking the test suite itself, which would then hide everything else. Deferred for the same reason as starlette. |
| `ecdsa` | 0.19.2 | *unchanged* | **No fixed version exists upstream at all** - this is a known, maintainer-acknowledged pure-Python timing-side-channel issue (Minerva-class) that the ecdsa project has stated it won't fix in pure Python. Nothing to bump to. |

**Frontend (`npm audit --production`):** 4 high-severity findings
(`nanoid`, `postcss`, `sharp`), all transitive through `next`. The only
fix path (`npm audit fix --force`) would install `next@16.3.0`, "outside
the stated dependency range" per npm's own output - a major Next.js
version bump, same class of risk as the backend's starlette/fastapi gap.
Not attempted this pass.

## Deliberately deferred - not silently ignored

The starlette/fastapi and Next.js major-version bumps are real,
documented gaps, not oversights. Both need dedicated time to:

1. Read the actual changelog/migration guide for breaking changes.
2. Bump in a branch, run the full test suite (backend: `pytest`, ~800+
   tests; frontend: `npm run lint && npx tsc --noEmit && npm test && npm
   run build`).
3. Manually smoke-test the areas most likely to be affected by a
   framework-level change (request/response handling, middleware,
   routing) beyond what automated tests catch.

Treat "the scan still shows these" as expected and already known, not as
a sign the scanning step is broken - re-check this doc before re-flagging
the same findings as new.

## Why not a hard gate yet

Making either scan step fail the build today would make CI permanently
red because of the framework-level findings above, which can't be fixed
in isolation without the dedicated upgrade work described above. A
security gate that's always red trains everyone to ignore it - worse than
no gate at all. Once the starlette/fastapi and Next.js upgrades land,
flip both steps to hard-fail (remove `continue-on-error: true`) so any
*new* vulnerability introduced afterward actually blocks the build.
