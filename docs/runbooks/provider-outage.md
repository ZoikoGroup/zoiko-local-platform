# Runbook: Third-Party Provider Outage

Every vendor integration lives behind a "Provider Gateway" module under
`backend/app/integrations/<category>/` (see root `CLAUDE.md`) and converts
the vendor's own exception into one of our custom exception types
(`TelecomError`, `VideoError`, `LLMError`, `StorageError`, `KYCError`,
`EmbeddingError`). Routes catch those and return a `502 Bad Gateway` with
the vendor's own error message — confirmed by this session's chaos-testing
pass: every call site into all six integrations degrades to a clean error
or a documented fallback, nothing crashes the process. So a provider outage
almost never looks like a 500 — it looks like a cluster of 502s (or, for a
couple of specific paths noted below, a silent degrade with no error at
all).

## First, confirm which provider

```
GET /ops/provider-status   (staff auth required)
```

Returns `{configured, ok, detail}` per provider. `configured: false` means a
missing/blank credential (a deploy config problem, not a real vendor
outage). `ok: false` with `configured: true` means the vendor's own health
check failed — a real outage or a bad/expired credential.

Then check `GET /ops/errors/summary?hours=1` — group by `path` to see which
feature area is actually affected (e.g. everything under `/media/voice/*`
failing points at Twilio; everything under `/media/video/*` points at
LiveKit).

## Per-provider

### Twilio (calling, SMS, number provisioning)
- **Env vars:** `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- **Symptoms:** 502s on `/numbers/purchase`, `/numbers/{e164}/cancel`,
  `/numbers/{e164}/sync-webhook`, `/media/voice/outbound`,
  `/staff/numbers/{id}/retry-provisioning`; SMS notifications silently
  logged as `failed` in `notification_deliveries` (this path never raises,
  it degrades — check that table, not the error log, for SMS-specific
  issues).
- **Real, non-outage cause to rule out first:** the account's own quota/
  trial limits. Confirmed live this session: a purchase attempt returned
  `502` with body `"HTTP 400 error: Unable to create record: Trial account
  has reached the maximum number of phone numbers allowed."` — that's
  Twilio working correctly and telling you the *account* needs upgrading,
  not a Twilio outage. Check the error message body before assuming
  outage.
- **Check:** https://status.twilio.com, and the Twilio console's own error
  logs for the account.
- **Numbers stuck mid-provisioning:** a purchase that fails after the
  number was already reserved reverts to `RESERVED` status automatically
  (`numbering/numbers/service.py`). Staff can see/retry these at
  `/staff/(console)/provisioning` (`GET /staff/numbers/stuck-provisioning`,
  `POST /staff/numbers/{id}/retry-provisioning`).

### LiveKit (video calling)
- **Env vars:** `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- **Symptoms:** 502s on `POST /media/video/rooms` (create), `.../end`,
  `.../recording/start`. A failed create/end leaves no orphaned
  `VideoSession` row (verified this session's chaos tests) — a failed
  recording start leaves `recording_egress_id` unset, so the room is *not*
  incorrectly shown as recording.
- **Check:** LiveKit Cloud project dashboard status, and that the webhook
  URL (`POST /media/video/webhook`) is still correctly configured in the
  LiveKit project settings — a misconfigured webhook doesn't 502 anything,
  it just silently stops syncing room state (sessions stay "active" after
  everyone's left). If call history shows rooms stuck `active` long after
  they should have ended, check the webhook config, not provider-status.

### Groq (LLM summaries + AI Receptionist qualification)
- **Env vars:** `GROQ_API_KEY`
- **Symptoms:** 502s on `/intelligence/{calls,voicemails,video-sessions}/*/summarize`.
  The AI Receptionist path (`POST /media/receptionist/respond`) does
  **not** 502 on a Groq failure — it degrades to capturing the raw
  transcript only (`caller_name`/`urgency`/`model_version` all null,
  `raw_transcript` still saved). If receptionist calls are all showing up
  with no extracted fields, that's this degrade path, not a bug — check
  `/ops/provider-status` for Groq specifically.
- **Check:** https://groq.com status page / their status page if published.

### Cohere (semantic search embeddings)
- **Env vars:** `COHERE_API_KEY`
- **Symptoms:** never a 502 — both call sites degrade silently
  (`intelligence/service.py`): a summary still saves without an embedding
  if generation fails, and semantic search returns an empty list instead of
  an error. **This means a Cohere outage is invisible unless you check
  `/ops/provider-status` or `/ops/errors`** (a failed embedding attempt
  during a summarize call does NOT create an error_event either, since it's
  caught before reaching a 5xx). If users report "search isn't finding
  anything," check Cohere status before assuming a search-quality problem.

### AWS S3 / S3-compatible storage (call/video recordings)
- **Env vars:** `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_BUCKET`,
  `S3_REGION`, `S3_ENDPOINT` (blank for real AWS S3; set for
  Cloudflare R2 or another S3-compatible provider)
- **Symptoms:** recording download links silently return `null` (not an
  error) in `GET /media/video/rooms`; video-session AI summarization
  returns a 502; retention purge marks the item `failed` (not deleted) and
  leaves `recording_url` untouched, safe to retry on the next scheduled
  purge run.
- **Check:** AWS Service Health Dashboard (or your provider's equivalent),
  and that the bucket/region/credentials in `fly secrets` actually match
  what's configured on the provider side — a renamed/deleted bucket looks
  identical to a real outage from the app's perspective.

### Stripe Identity (KYC/KYB verification)
- **Env vars:** `STRIPE_SECRET_KEY`, `STRIPE_IDENTITY_WEBHOOK_SECRET`
- **Symptoms:** 502 on `POST /compliance/cases/{id}/kyc/start`. The webhook
  (`POST /compliance/webhooks/stripe-identity`) returns 403 on a bad/missing
  signature — if Stripe's dashboard shows webhook delivery failures with a
  403, first confirm `STRIPE_IDENTITY_WEBHOOK_SECRET` matches what's
  configured on the Stripe side (a secret rotation on one side without the
  other is indistinguishable from an attack from the app's logs alone).
- **Check:** https://status.stripe.com

### Resend (transactional email)
- **Env vars:** `RESEND_API_KEY`, `EMAIL_FROM_ADDRESS`
- **Symptoms:** notifications logged as `failed` in `notification_deliveries`
  (never a 502 — email send failures don't block the state-changing action
  that triggered them, e.g. a team member is still added even if the
  "you've been added" email fails to send).
- **Check:** https://resend-status.com, and that `EMAIL_FROM_ADDRESS`'s
  domain still has valid SPF/DKIM/DMARC records (a DNS change on the
  sending domain can silently break delivery with no error on our side —
  Resend will show bounces in its own dashboard, not in ours).

## After the vendor recovers

Nothing needs to be manually replayed for most of these — the next request
just succeeds. Exceptions:
- **Twilio recording purge failures** (`retention/service.py`) — the daily
  purge job will retry automatically next run, since `recording_url` was
  left untouched on failure. No manual action needed.
- **A number stuck in `RESERVED`** after a failed Twilio purchase — use the
  staff provisioning-recovery queue to retry or release it.
- **A dead LiveKit webhook** — once fixed, any room that's stuck `active`
  will need a manual `POST /media/video/rooms/{name}/end` (as the account
  that owns it, or staff) to correct the DB state; LiveKit itself has
  already closed the actual room.
