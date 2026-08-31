# Zoiko Local — What We Built, Feature by Feature, and What We Still Can't Do

This is a precise, honest ledger: every real feature that exists today, exactly what it does, and — just as important — every real thing the platform still cannot do. Nothing below is guessed; everything was verified against the real code and real live tests.

**Correction (2026-08-31):** This doc had gone stale on the single biggest claim in it — Part 2, Items 1 and 2 below used to say live two-way outbound calling and in-browser call answering didn't exist. Both are real and working today via real, live-tested browser calling (`@twilio/voice-sdk`, wired end-to-end into the dashboard — `POST /media/voice/browser-token` + `POST /media/voice/browser-connect` on the backend, `frontend/src/lib/voiceDevice.tsx` on the frontend, live in both the dashboard layout — so an incoming call can ring your browser tab from anywhere in the app — and the Calls page, for placing one). This was true before this correction was written; the doc just hadn't caught up. See the rewritten Items 2 and 3 in Part 1, and the removed items in Part 2, below. Two more real features shipped since the last full rewrite of this doc: **call transfer** (blind/cold transfer of a live in-progress call to a new destination) and **app-to-app voice calling** (calling another Zoiko Local customer's number browser-to-browser, with no carrier leg, that still respects *their* configured call routing rather than bypassing it) — both added as Part 1 items below.

---

## PART 1 — Everything we built, explained in full

### 1. Phone Numbers
- **Search and buy a real local phone number** in 8 countries: United States, United Kingdom, Canada, Australia, Germany, France, India, Singapore.
- The flow is: **Search → Reserve → (Verify, if required) → Checkout.**
- Most countries (US, India, Canada, Australia, Germany, France, Singapore) activate the number **instantly** after purchase.
- The UK requires a real **identity verification step** first: you upload a government ID, submit it to Twilio for real review, and the number only activates once approved. This is a real UK telecom regulation, not something we invented.
- You can **suspend** a number (temporarily disable it), **cancel** it, or **port in** a number you already own from another provider.
- Every number shows its live status on the "My Numbers" page: Active, Compliance Pending, Suspended, or Cancelled.

### 2. Receiving Calls (Inbound) — real, live, two-way conversation
This is the core of the platform, and it fully works today. When someone dials your Zoiko Local number, one of these happens, in this order:
1. **IVR menu**, if you've set one up — caller hears "Press 1 for Sales, 2 for Support," etc.
2. **Ring Group / Forwarding** — the call rings one real phone, or several real phones at once (and your browser dashboard tab too, if you have it open — see below), and whoever picks up first gets the call.
3. **AI Receptionist**, if enabled and no one answers the forward — an AI answers, has a real conversation to understand why they're calling, and can escalate urgent calls to a real person.
4. **Voicemail**, if nothing else is set up — the caller leaves a message.

Whoever ends up answering — a real human on a real phone, **or a real human answering right inside the browser dashboard, no phone needed** — has a **completely normal, live, two-way telephone conversation** with the caller — full duplex, real-time, exactly like an ordinary phone call. This is proven and tested today with real calls.

### 3. Making Calls (Outbound) — real, live, two-way conversation, straight from the browser
The dashboard's "Make a Call" tool (on the Calls page) places a **real live phone call directly from your browser's own microphone and speaker** — no phone needed on your end, and no announcement/one-way limitation. Concretely:
- Your browser holds a short-lived Twilio Voice access token (`GET /media/voice/browser-token`) and places the call itself via `@twilio/voice-sdk`'s `Device.connect()`.
- Twilio dials the real destination number over the carrier network and bridges it to your browser's audio.
- Once they answer, **you talk, they talk back** — full duplex, real-time, exactly like Video Calling's in-browser audio, just over a phone line instead of the internet on their end.

This also means answering an inbound call from inside the dashboard (Part 1, Item 2 above) and placing an outbound call from the dashboard both go through the exact same real, live audio path — the same one Twilio Voice SDK connection, just triggered by an incoming vs. an outgoing call.

### 4. Voicemail
- Any unanswered inbound call can leave a real voicemail recording.
- Every voicemail is automatically transcribed and summarized by AI.
- Voicemails show up per-number on the Voicemail page.

### 5. SMS (Text Messaging)
- Send and receive real SMS text messages from your Zoiko Local number.
- Conversations are threaded per contact number.
- **Real limitation, confirmed live:** some countries (the UK, confirmed today) block incoming texts from a foreign country's phone number, as an anti-spam rule enforced by their own phone carriers. This has nothing to do with Zoiko Local's settings — it means you need a number that's local to the country you're texting into.

### 6. WhatsApp Business Messaging
- The code supports sending/receiving WhatsApp Business messages exactly like SMS.
- **Not usable yet for any number** — WhatsApp requires a real approval process from Meta (Facebook's parent company) before any number can send messages, and that approval has not been started for any of our numbers yet.

### 7. AI Receptionist
- A real, working AI that can answer your calls automatically instead of a human.
- It greets the caller, asks questions to understand why they're calling, and can escalate urgent calls straight to a nominated real team member.
- Has built-in guardrails so it will never promise a price, make a legal commitment, or give medical advice.
- Requires a paid plan add-on to turn on for a number.

### 8. Video Calling
- A real, live, two-way conversation directly inside the browser dashboard — full camera + microphone, Google Meet-style interface, with reactions.
- 1:1 and small-group video rooms both work.
- The same "real, live, in-browser conversation" capability this proved out first is now also how phone calling works in the browser (Items 2 and 3 above) — this was the technical proof it was achievable on this platform, and it's since been extended to phone audio too.

### 9. Call Flows & IVR Builder
- Build a visual "menu tree": caller presses a digit, gets routed to a different destination, sub-menu, voicemail, or a fallback if they don't press anything.
- Supports business-hours-aware routing (different behavior during vs. outside working hours).

### 10. Ring Groups
- Configure multiple real phone numbers to all ring simultaneously for one incoming call — first to answer gets it.

### 11. Call Queues
- Callers wait in a queue; available team members can pull the next waiting caller — a basic call-center feature.

### 12. Contacts (mini-CRM)
- Store a customer's name, phone number, email, and notes.
- See their **entire history in one place** — every call, SMS, and voicemail with them, automatically pulled together.

### 13. Compliance & Identity Verification
- Country-specific rules (like the UK's ID-check requirement) are enforced automatically — the system physically won't let a purchase go through until the right approval exists.
- Every account action is written to a permanent audit trail.

### 14. Billing & Entitlements
- Real subscription plans: Free Trial, Starter, Business, Enterprise.
- Usage is metered (minutes, messages, numbers) and checked against plan limits before allowing an action.
- **New this session:** free-trial accounts can now *view* every page, but cannot perform any paid action (buy a number, place a call, send a message) until they upgrade to a paid plan.
- **Real limitation:** actual payment collection is not live — Stripe is still in test mode, so no real money can be charged to a real customer yet.

### 15. AI Call & Voicemail Summaries
- Every recorded call and every voicemail can get a real AI-written summary and a searchable transcript.

### 16. Fraud & Risk Protection
- Automatically detects suspicious patterns: calling a known-fraud destination, calling too fast/too many places at once, spam-like inbound patterns — and blocks or flags them before real cost is incurred.

### 17. Call Transfer
- While a call is live (in-progress, either an inbound call you answered or an outbound call you placed from the browser), you can transfer it to a new destination — a blind/cold transfer: the call is redirected, not conferenced in with you still on the line.
- Requires a Business plan or higher.

### 18. App-to-App Voice Calling
- Calling another Zoiko Local customer's number, browser-to-browser, skips the carrier/PSTN leg entirely — but **does not bypass their own call setup.** It's routed through the exact same ring-group/business-hours/AI-Receptionist/voicemail configuration a normal inbound call to them would hit — it's only the *transport* that's different (no phone network in the middle), not their routing.
- Included on every plan. If the receiving number isn't eligible for this (wrong entitlement, inactive, etc.), the call transparently falls back to a normal outbound call to that number instead of failing.

---

## PART 2 — What we CANNOT do (the honest list)

*(Two items used to live here — live two-way outbound conversation, and answering an inbound call from inside the dashboard. Both are real, working features now — see Part 1, Items 2, 3, and the "Correction" note at the top of this doc. Removed rather than left as false claims.)*

### ❌ 1. WhatsApp messaging
Built in code, but blocked until real approval comes through from Meta — not something we can turn on ourselves.

### ❌ 2. Texting into the UK from a non-UK number
Confirmed live: the UK's phone network blocks SMS from foreign numbers. Needs a real UK number to text UK recipients reliably.

### ❌ 3. Real money / live payments
Stripe is in test mode. No real customer can be charged real money yet — that requires completing Stripe's business verification (a manual step only the founder can do).

### ❌ 4. Full ZoikoNex billing connection
The integration code is real and tested end-to-end against a locally self-hosted copy of ZoikoNex (not a mock), but there's no live connection to any real production ZoikoNex server yet, and two real bugs in ZoikoNex's own code (payment capture, closing a billing period) block full end-to-end billing even once connected.

### ❌ 5. Mobile apps (iOS / Android)
**The biggest gap of all.** The original project plan explicitly requires native iOS and Android apps — quoting the roadmap doc directly: *"Web dashboard, iOS app and Android app. Mobile calling experience cannot be deferred,"* with 4 dedicated mobile engineers budgeted for it.

**What exists today: nothing.** No iOS app, no Android app, no APK file — only the web dashboard. This was never started, not partially built. This needs a real founder-level decision: either formally re-scope to "web-only for now," or actually start mobile development as a real, large project.

---

## Bottom line

| Ask | Status |
|---|---|
| Buy a number and receive real two-way calls | ✅ Done, working today |
| Buy a number and place real two-way calls | ✅ Done, working today — live, from the browser |
| Talk live inside the browser | ✅ Works today for both **Video** and **phone calls** |
| Transfer a live call to someone else | ✅ Done, working today (blind transfer, Business plan+) |
| Call another Zoiko Local customer app-to-app, no carrier leg | ✅ Done, working today — still respects their own call routing |
| Native mobile apps | ❌ Not started at all |
| Real live payments | ❌ Test mode only |
| WhatsApp | ❌ Blocked on Meta's approval, not on our code |
