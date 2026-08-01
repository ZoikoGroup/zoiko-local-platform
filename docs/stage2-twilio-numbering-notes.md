# Stage 2 Prep — Twilio Numbering API Notes & Endpoint Draft

Prepared ahead of Stage 2 (Number Inventory + Twilio integration). Goal: understand how
Twilio's numbering API actually behaves so Stage 2 can start coding the moment Stage 1
(identity/auth) merges, without losing time re-learning the vendor API.

No Stage 2 app code was written for this — that belongs in
`backend/app/integrations/telecom/twilio.py` once Stage 1 merges (Provider Gateway
pattern — only files in `integrations/telecom/` may import the Twilio SDK directly).
A standalone reference script that reproduces every finding below live is at
`scripts/twilio_numbering_explore.py` (`search`, `coverage`, `owned` subcommands) —
safe to run today since it lives outside `backend/app/` and has no dependency on
Stage 1's models.

## Trial account status

- Account SID/Auth Token verified working (`GET /Accounts/{Sid}.json` → 200, `status: active`, `type: Trial`).
- Trial balance: **$15.50 USD**.
- **0 numbers currently owned** (`IncomingPhoneNumbers.json` → empty list). Nothing is
  auto-provisioned just by signing up — a number has to be explicitly purchased.
- `+916305101934` is registered as a **Verified Caller ID** (`OutgoingCallerIds.json`),
  i.e. a personal number trial calls/SMS are allowed to reach — not a Twilio-owned number.
- Trial accounts can only call/text verified caller IDs; everything else is rejected until
  the account is upgraded to paid.

## API behavior findings

### Search — `GET /AvailablePhoneNumbers/{Country}/Local.json`
Tested live with `AreaCode=628`. Confirmed:
- Returns a **live snapshot** of numbers Twilio considers available right now — `phone_number`,
  `friendly_name`, `locality`, `region`, `iso_country`, `capabilities` (voice/SMS/MMS),
  `address_requirements` (`none` / `local` / `foreign`).
- **No hold, no ID, no reservation token.** A number in this list can be bought by anyone,
  at any time, until someone does. There is nothing to "reserve" on Twilio's side.
- `address_requirements` matters directly for our Compliance Rules table — some
  countries require identity/address docs before a number in that list is purchasable.
- **No price in the response.** Cost has to come from a separate call.

### Pricing — `GET pricing.twilio.com/v1/PhoneNumbers/Countries/{Country}.json`
Tested live — returned all-null fields on this trial account. Likely restricted until
the account is upgraded, or the endpoint needs different query params. **Open question
for Stage 2**: either upgrade a test account to confirm, or hardcode Twilio's published
list pricing as a placeholder and revisit before billing integration (Stage 6).

### Buy — `POST /IncomingPhoneNumbers.json` (not executed, by choice — costs trial credit)
Per Twilio docs: takes `PhoneNumber` (E.164), returns a `sid`, `phone_number`,
`capabilities`, and webhook config fields (`voice_url`, `sms_url`, etc.) once purchased.
This is Twilio's only atomic numbering action — search and buy, nothing in between.

### List owned / Release — `GET` and `DELETE /IncomingPhoneNumbers/{Sid}.json`
Standard CRUD once a number is owned; not yet exercised against a real purchase.

## Launch-market coverage check (against roadmap Tier A/B markets)

Tested live search against every Phase 1 launch market named in the roadmap doc.
Results:

| Market | Twilio coverage | address_requirements | Capabilities (voice/SMS/MMS) |
|---|---|---|---|
| US | ✅ Local numbers available | none | voice ✅ SMS ✅ MMS ✅ |
| CA | ✅ Local numbers available | none | voice ✅ SMS ✅ MMS ✅ |
| GB | ✅ Local numbers available | **local** (address doc required) | voice ✅ SMS ❌ MMS ❌ |
| MX | ✅ Local numbers available | **local** (address doc required) | voice ✅ SMS ❌ MMS ❌ |
| ZA | ✅ Local numbers available | **any** (some address doc required) | voice ✅ SMS ❌ MMS ❌ |
| **NG** | ❌ **Not a supported country at all** (404 on both country lookup and Local/Mobile search) | n/a | n/a |
| **KE** | ❌ **Not a supported country at all** | n/a | n/a |
| **GH** | ❌ **Not a supported country at all** | n/a | n/a |

**This is a roadmap-level flag, not just an engineering detail.** Three of the five Tier B
"high-value growth corridors" (Nigeria, Kenya, Ghana) have zero Twilio numbering coverage
— confirmed at the country-list level, not just an empty inventory. Per the roadmap's own
market rule ("no country goes live... unless numbering supply confirmed"), these three
markets **cannot launch on Twilio alone**. Options, for the CTO/Product decision this
roadmap already calls for (§15 action items — "Shortlist CPaaS... providers"):

- Add a second telecom provider behind the Provider Gateway specifically for
  African markets (e.g. Africa's Talking, Infobip — not yet researched, just flagging
  the need) — this is exactly the scenario the Provider Gateway pattern was built for.
- Or explicitly sequence NG/KE/GH into a later Phase 1 wave once a provider is
  confirmed, per the roadmap's own fallback clause for Tier B markets.

Also worth noting: **GB, MX, and ZA `Local`-type numbers came back with no SMS/MMS
capability** — only voice. Confirmed this is a **number-type issue, not a country-wide
one**: `GB/Mobile.json` search returns numbers with `SMS: true` (still no MMS), and
`address_requirements: none` instead of `local`. This matches a known UK regulatory
distinction — geographic/landline-type numbers can't carry SMS there, only mobile-type
can. **Design implication:** our `/numbers/search` endpoint can't just take a country —
it needs a number-type parameter (`local` vs `mobile` vs `tollfree`), and the Compliance
Rules table needs capability + address-requirement data keyed on `(country, number_type)`,
not just `country`. Worth re-checking MX/ZA Mobile type too before Stage 2 coding starts.

### Error-handling behavior (relevant to designing our own error responses)
- Unsupported/invalid country (`ZZ`) → **HTTP 404**, Twilio error body
  `{ code: 20404, message, more_info, status }`. Our `/numbers/search` should translate
  this into a clean 4xx with a "country not supported" reason, not pass Twilio's raw
  error through.
- Valid country + no matches (nonexistent area code) → **HTTP 200, empty `available_phone_numbers: []`**,
  not an error. Our endpoint should mirror this — "no results" is not a failure state.

## Key architectural implication

Twilio has no concept matching our **Reserved** lifecycle state (see
`Zoiko_Local_Backend_Architecture.docx` §7 number lifecycle: Available → Reserved →
Purchase Pending → Compliance Pending → Provisioning → Active). That entire state has to
be **built by us**, sitting in front of Twilio's buy call:

- Reservation is a Zoiko-only DB row + Redis TTL lock (default 12 min per the arch doc),
  keyed on the candidate `phone_number` string (Twilio has no reservation ID to key off).
- Twilio's search results are not guaranteed to still be available by the time a customer
  confirms checkout — the buy step must handle "already taken" as an expected error path,
  not an edge case, and return the number to a controlled state rather than instantly
  back to public search results (per the arch doc's atomicity law).

## Draft endpoint list for Stage 2

These are proposed internal API contracts, not final — for Person A/whoever picks up
Stage 2 to refine once identity/auth (Stage 1) is in place.

### `POST /numbers/search`
Request:
```json
{ "country": "US", "number_type": "local", "area_code": "628", "contains": null, "capabilities": ["voice", "sms"] }
```
`number_type` (`local` / `mobile` / `tollfree`) is required, not optional — confirmed via
the GB Local-vs-Mobile SMS-capability difference above. Unsupported country → clean 4xx
with reason, not Twilio's raw error passthrough. No matches → 200 with empty results.
Calls `integrations/telecom/twilio.py: search_available_numbers()`. Response:
```json
{
  "results": [
    {
      "phone_number": "+16282126391",
      "locality": "Corte Madera",
      "region": "CA",
      "capabilities": { "voice": true, "sms": true, "mms": true },
      "address_requirements": "none"
    }
  ]
}
```
No DB write. Pure passthrough to Twilio, shaped for our frontend.

### `POST /numbers/reserve`
Request:
```json
{ "phone_number": "+16282126391", "account_id": "..." }
```
Zoiko-only — **no Twilio call**. Creates a `Number Reservation` row (per the Data Model
in the arch doc: `reservation_id, number_id, account_id, expires_at, lock_token, status`)
and a Redis TTL lock. Response:
```json
{ "reservation_id": "...", "expires_at": "2026-07-30T10:05:00Z", "lock_token": "..." }
```

### `POST /numbers/buy`
Request:
```json
{ "reservation_id": "...", "lock_token": "..." }
```
Validates the reservation hasn't expired and the lock token matches → transitions to
Purchase Pending → calls Twilio `IncomingPhoneNumbers.create()` → on success, writes the
`Local Number` record (Provisioning → Active) and fires `number.activated`. On Twilio
failure (number already taken, compliance block, etc.), the reservation is released and
an explicit error reason is returned — never a silent failure, per the arch doc's
event-architecture doctrine. Response:
```json
{ "number_id": "...", "e164": "+16282126391", "status": "active", "twilio_sid": "PN..." }
```

## Open items for whoever builds Stage 2

- Resolve the Pricing API null-response issue before wiring billing/entitlements.
- Decide reservation TTL enforcement mechanism (Redis vs DB-only) — arch doc recommends
  Redis for the lock, Postgres for the durable reservation row.
- Confirm behavior when a reserved number gets bought by someone else outside Zoiko
  (shouldn't be possible once Twilio purchase succeeds, but the race exists between
  search and reserve since Twilio has no hold).
