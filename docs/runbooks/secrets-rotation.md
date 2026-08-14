# Runbook: Secrets Rotation

Production Readiness & Go-Live Decision Standard §A10 asks for "secrets
rotation" as a launch-gate control. This covers what's actually rotatable
in this codebase today, and the one secret (JWT signing key) that needed
real code support to rotate without a mass logout.

## JWT signing key (`JWT_SECRET_KEY`)

Access tokens live up to 24h (`ACCESS_TOKEN_EXPIRE_MINUTES` in
`app/core/security.py`). Naively swapping `JWT_SECRET_KEY` to a new value
would make every token issued in the last 24h fail verification
instantly - every logged-in user gets kicked out at once, not a graceful
rotation.

`app.core.security.decode_access_token` tries `JWT_SECRET_KEY` first,
then falls back to `JWT_SECRET_KEY_PREVIOUS` if set and non-empty. This
is what makes the rotation below possible without a mass logout:

1. Generate a new secret: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Set `JWT_SECRET_KEY_PREVIOUS` to the CURRENT (about-to-be-replaced)
   value of `JWT_SECRET_KEY`.
3. Set `JWT_SECRET_KEY` to the new value generated in step 1.
4. Deploy both changes together. New tokens are signed with the new key;
   tokens already issued under the old key keep verifying via the
   `_previous` fallback.
5. Wait at least `ACCESS_TOKEN_EXPIRE_MINUTES` (24h) - every token issued
   under the old key will have naturally expired by then.
6. Clear `JWT_SECRET_KEY_PREVIOUS` back to empty and deploy again. This is
   not optional cleanup - leaving an old key valid forever defeats the
   point of rotating in the first place.

Rotate immediately (don't wait for a scheduled rotation) if the current
`JWT_SECRET_KEY` is ever suspected to have leaked - e.g. committed to git,
printed in a log, or exposed via any other channel.

## Database credentials (`DATABASE_URL`)

No code-level rotation support needed here - SQLAlchemy's engine reads
`DATABASE_URL` at process startup, so rotating the Postgres password is:
update the password in Postgres itself, update `DATABASE_URL` in the
environment, restart the app process(es). There's no "old and new both
valid" window to manage since this isn't a stateless token scheme - the
brief unavailability during restart is the same as any other deploy.

## Third-party API keys (Stripe, Twilio, Groq, Cohere, etc.)

Each Provider Gateway module (`app/integrations/<category>/`) reads its
key from `settings` at call time, not at import time - rotating any of
these is: generate a new key in the provider's own dashboard, update the
corresponding env var, restart. Revoke the old key in the provider's
dashboard once you've confirmed the new one works - an unrevoked old key
is a live credential that still works for anyone who has it.

## What's NOT covered here

A key-management service (AWS KMS/Vault-style automatic rotation) isn't
built - every rotation above is a manual, deliberate action, not
scheduled automatically. That's a reasonable gap at this stage (no
secrets manager is wired in yet at all), not something to fake with a
cron job that has nowhere real to report to.
