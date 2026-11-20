"""Seed content for the public website assistant (WF-01..WF-13, v1.0,
24 October 2026). Single source of truth for three consumers:

1. The Alembic migration that first creates the tables inserts these rows.
2. tests/test_chatbot.py seeds the same rows via the ORM (the test
   schema is built with Base.metadata.create_all, which doesn't run
   migrations) - so tests exercise exactly what production seeds.
3. Nothing at runtime imports this module - the service layer reads the
   DB tables only, so live staff edits always win over this file.

Copy discipline (per the wireframes' own rules): every body text below is
either (a) approved-draft copy taken from the docs' copy decks, (b) a
qualified statement that never claims availability/price/eligibility
without an authoritative source, or (c) a WF-13 safety script that must
never be paraphrased by anything downstream. The publisher is recorded as
the "Zoiko Local" product name only - the docs' open legal-entity
attribution issue (Zoiko Communications Group Inc. trading name vs.
Zoiko Tech Inc.) is deliberately NOT resolved by inventing an attribution
here; rows carry the product-page branding, which is what the cited
zoikolocal.com pages themselves carry today.

Destinations map to real routes in the marketing-site repo
(github.com/ZoikoGroup/zoiko-local-react) - checked against its app/
directory when this file was written.
"""

# Content version = the wireframe set this content was authored against.
# Surfaced to clients in every bootstrap (WF-01 "content_version").
CONTENT_VERSION = "wf-2026-11-19.2"

# WF-12: en-US is the only reviewed, approved locale. Everything else
# fails closed to en-US in the service layer (never machine-translated).
SUPPORTED_LOCALES = ["en-US"]
DEFAULT_LOCALE = "en-US"

# WF-01: assistant identity strings - resolved from data, never hardcoded
# at call sites ("Locu" is an internal working name and must never appear
# in any customer-facing surface per the docs' brand rule).
ASSISTANT_NAME = "Zoiko Local Assistant"
AI_DISCLOSURE = (
    "This assistant is automated and uses approved Zoiko Local data. It is not "
    "a live agent and cannot place calls, change your account, take payments, "
    "or confirm live number availability."
)
GREETING_RESPONSE_TEXT = (
    "Hi! I'm the Zoiko Local Assistant. I can help you find a local number, "
    "understand calling and the AI Receptionist, compare plans and pricing, "
    "start a port, or check getting-started guidance. What would you like to do?"
)
GENERAL_FAQ = [
    {
        "key": "about",
        "patterns": [r"\b(what is|tell me about|about)\s+zoiko(\s+local)?\b", r"\bwho are you\b"],
        "text": (
            "Zoiko Local provides local business numbers, cloud calling with call flows and "
            "voicemail, an AI Receptionist, video meetings, number porting, and guided setup. "
            "I can explain these services and link you to the official pages; I cannot confirm "
            "live availability in chat."
        ),
        "source_slugs": [
            "global-coverage-page",
            "get-local-number-product-page",
            "cloud-calling-product-page",
            "ai-receptionist-product-page",
            "video-product-page",
        ],
        "actions": [("GET_LOCAL_NUMBER", "primary"), ("CLOUD_CALLING", "secondary")],
        "suggestions": [
            {"label": "Find a number", "value": "NUMBER_DISCOVERY", "kind": "intent"},
            {"label": "How calling works", "value": "CALLING_OVERVIEW", "kind": "intent"},
        ],
    },
    {
        "key": "capabilities",
        "patterns": [r"\bwhat can you do\b", r"\bhow can you help\b", r"\bwhat do you (do|offer|support)\b"],
        "text": (
            "I can guide you through local number discovery, calling, the AI Receptionist, "
            "video meetings, plans and pricing, porting, activation, privacy, and "
            "emergency-calling information. I cannot place calls, join meetings, change an "
            "account, take payments, or confirm live availability."
        ),
        "source_slugs": [
            "get-local-number-product-page",
            "cloud-calling-product-page",
            "ai-receptionist-product-page",
            "video-product-page",
            "pricing-product-page",
        ],
        "actions": [("GET_LOCAL_NUMBER", "primary"), ("PRICING", "secondary")],
        "suggestions": [
            {"label": "See plans", "value": "PLANS_OVERVIEW", "kind": "intent"},
            {"label": "AI Receptionist", "value": "RECEPTIONIST_OVERVIEW", "kind": "intent"},
        ],
    },
    {
        "key": "get_number",
        "patterns": [r"\bhow do i (get|buy|purchase|acquire|find)\b.{0,30}\b(number|numbers)\b", r"\bwhere (do|can) i (get|buy) (a\s+)?(local\s+)?numbers?\b"],
        "text": (
            "Start with number discovery: choose a market and customer type, then follow the "
            "verification and activation steps on the official Get a Local Number page. Live "
            "availability is confirmed there, not in chat."
        ),
        "source_slugs": ["get-local-number-product-page", "verification-help-overview", "activation-help-overview"],
        "actions": [("GET_LOCAL_NUMBER", "primary")],
        "suggestions": [{"label": "Start number discovery", "value": "NUMBER_DISCOVERY", "kind": "intent"}],
    },
    {
        "key": "pricing_basics",
        "patterns": [r"\bhow (does|do) pricing work\b", r"\bwhat (affects|is included in) (the\s+)?(price|pricing|total)\b"],
        "text": (
            "Plan prices shown here come from Zoiko Local's approved price catalog. Your final "
            "total can also depend on number rental and usage, and is calculated during secure "
            "checkout; I never quote a custom total."
        ),
        "source_slugs": ["pricing-product-page", "get-local-number-product-page"],
        "actions": [("PRICING", "primary")],
        "suggestions": [{"label": "See current plans", "value": "PLANS_OVERVIEW", "kind": "intent"}],
    },
    {
        "key": "video",
        "patterns": [r"\bvideo\b", r"\b(meetings?|video calls?)\b"],
        "text": (
            "Zoiko Local includes video meetings for supported workspaces. Availability can "
            "depend on the plan, so I can link you to the official Video page; I cannot start "
            "or join a meeting from chat."
        ),
        "source_slugs": ["video-product-page"],
        "actions": [("VIDEO", "primary"), ("PRICING", "secondary")],
        "suggestions": [{"label": "See plans", "value": "PLANS_OVERVIEW", "kind": "intent"}],
    },
    {
        "key": "receptionist_features",
        "patterns": [r"\b(how does|what is|tell me about)\b.{0,20}\b(ai\s+)?receptionist\b", r"\bwhat can (the|your) (ai\s+)?receptionist do\b"],
        "text": (
            "The AI Receptionist is a per-number feature that answers calls to your Zoiko Local "
            "number, greets callers, asks what they need, takes messages, and can escalate urgent "
            "calls to a team member you nominate. Setup happens in the dashboard; I cannot "
            "configure it in chat."
        ),
        "source_slugs": ["ai-receptionist-product-page", "ai-receptionist-setup-help"],
        "actions": [("AI_RECEPTIONIST", "primary")],
        "suggestions": [{"label": "See a fictional example", "value": "RECEPTIONIST_EXAMPLE", "kind": "intent"}],
    },
    {
        "key": "calling_features",
        "patterns": [r"\b(how does|what is|tell me about)\b.{0,20}\b(cloud\s+)?calling\b", r"\bcall flows?\b", r"\bvoicemail\b"],
        "text": (
            "Zoiko Local cloud calling lets you make and receive calls on your local number from "
            "the browser dashboard or the Zoiko Local app, with call flows, voicemail, and "
            "optional recording. I cannot place calls, configure call flows, or quote your exact "
            "call costs."
        ),
        "source_slugs": ["cloud-calling-product-page", "calling-considerations-help"],
        "actions": [("CLOUD_CALLING", "primary")],
        "suggestions": [{"label": "Get a local number", "value": "NUMBER_DISCOVERY", "kind": "intent"}],
    },
    {
        "key": "porting_basics",
        "patterns": [r"\bwhat is (number\s+)?porting\b", r"\bbring (my|an existing) number\b", r"\btransfer (my|an existing) number\b"],
        "text": (
            "You may be able to bring an existing number to Zoiko Local. Porting depends on the "
            "country, your current carrier, and their release process. I cannot submit a port, "
            "collect authorization documents, or guarantee a completion date."
        ),
        "source_slugs": ["porting-product-page", "porting-authorization-help"],
        "actions": [("PORTING", "primary")],
        "suggestions": [],
    },
    {
        "key": "activation_steps",
        "patterns": [r"\bhow do i (get started|start|activate|set up)\b", r"\bgetting started\b", r"\bactivation steps\b"],
        "text": (
            "Create a free Zoiko Local account, pick your country and local number, complete any "
            "verification that country requires, and the number activates once the checks pass. "
            "No card is needed to explore."
        ),
        "source_slugs": ["activation-help-overview", "first-call-readiness-help"],
        "actions": [("START_FREE", "primary"), ("GET_LOCAL_NUMBER", "secondary")],
        "suggestions": [{"label": "Find a number", "value": "NUMBER_DISCOVERY", "kind": "intent"}],
    },
    {
        "key": "privacy",
        "patterns": [r"\bprivacy\b", r"\b(what|how) (data|information) (do you|is) (collect|store|keep|handle)\b"],
        "text": (
            "Your chat stays in the browser for the session, and you can clear the chat view or "
            "end the session at any time. Please don't share sensitive details like card numbers, "
            "passwords, or one-time codes in chat. The Privacy Policy explains what Zoiko Local "
            "collects."
        ),
        "source_slugs": ["privacy-policy-page"],
        "actions": [("PRIVACY_POLICY", "primary")],
        "suggestions": [],
    },
    {
        "key": "emergency",
        "patterns": [
            r"\bemergency calling\b",
            r"\b(what|how|why|when)\b.*\bemergency\b",
            r"\b(what|how|why|when)\b.*\b(911|112|999)\b",
        ],
        "text": (
            "Zoiko Local numbers are internet-based and are not a replacement for emergency "
            "calling. If you are in danger, contact your local emergency number directly. The "
            "Emergency Calling page explains what to consider before relying on any "
            "internet-based number."
        ),
        "source_slugs": ["emergency-calling-product-page"],
        "actions": [("EMERGENCY_CALLING", "primary")],
        "suggestions": [],
    },
    {
        "key": "support",
        "patterns": [r"\b(contact|talk to|reach)\s+(support|a person|someone|human)\b"],
        "text": (
            "I can answer guided questions here, or you can use the official Contact Support "
            "page for account-specific help."
        ),
        "source_slugs": ["contact-support-page"],
        "actions": [("CONTACT_SUPPORT", "primary"), ("CONTACT_SALES", "secondary")],
        "suggestions": [],
    },
]
SAFETY_NOTICE = (
    "Zoiko Local numbers are not a replacement for emergency calling. "
    "In an emergency, contact your local emergency number (such as 911, "
    "112, or 999) directly."
)

# Session lifetime (WF-01: "short bounded TTL"), and how long ended/expired
# sessions linger before the daily purge deletes them outright (data
# minimization - nothing to retain, so retention is as short as practical).
SESSION_TTL_MINUTES = 120
SESSION_PURGE_GRACE_HOURS = 24

INTENTS = [
    # --- The six WF-01 fixed quick-start intents, in the doc's own order ---
    {
        "intent_id": "NUMBER_DISCOVERY",
        "title": "Find a local number",
        "description": "Explore countries, number types and verification before buying.",
        "keywords": [
            "number", "numbers", "local number", "phone number", "get a number",
            "buy a number", "new number", "virtual number", "toll-free", "toll free",
            "coverage", "countries", "available", "availability", "inventory",
        ],
        "sort_order": 10,
        "is_quick_start": True,
    },
    {
        "intent_id": "RECEPTIONIST_OVERVIEW",
        "title": "Explore the AI Receptionist",
        "description": "What the AI Receptionist can answer, route and hand off.",
        "keywords": [
            "receptionist", "ai receptionist", "answer my calls", "answering service",
            "call answering", "answer calls for me", "virtual receptionist",
        ],
        "sort_order": 20,
        "is_quick_start": True,
    },
    {
        "intent_id": "CALLING_OVERVIEW",
        "title": "How calling works",
        "description": "Cloud calling from your browser or app on a local number.",
        "keywords": [
            "calling", "calls", "call", "make calls", "cloud calling", "dial",
            "international calling", "minutes", "phone calls", "outbound",
        ],
        "sort_order": 30,
        "is_quick_start": True,
    },
    {
        "intent_id": "PLANS_OVERVIEW",
        "title": "Plans & pricing",
        "description": "Current approved plan prices and what affects your total.",
        "keywords": [
            "pricing", "price", "prices", "plans", "plan", "cost", "costs", "how much",
            "subscription", "billing", "monthly fee", "per month", "cheapest", "tiers",
        ],
        "sort_order": 40,
        "is_quick_start": True,
    },
    {
        "intent_id": "ACTIVATION_GUIDE",
        "title": "Get started",
        "description": "From free account to an activated number and first call.",
        "keywords": [
            "get started", "getting started", "start", "activate", "activation",
            "sign up", "signup", "register", "onboard", "first call", "setup",
            "set up", "getting a number",
        ],
        "sort_order": 50,
        "is_quick_start": True,
    },
    {
        "intent_id": "CONTACT_HANDOFF",
        "title": "Talk to a person",
        "description": "Route to the sales or support team for anything human.",
        "keywords": [
            "human", "person", "agent", "support", "contact", "talk to someone",
            "talk to a person", "sales", "representative", "real person", "help me",
            "urgent", "complaint", "refund",
        ],
        "sort_order": 60,
        "is_quick_start": True,
    },
    # --- Classifier-only intents (routed via free text / flow, no chip) ---
    {
        "intent_id": "PORTING_GUIDE",
        "title": "Number porting",
        "description": "Bringing an existing number to Zoiko Local.",
        # Checked BEFORE NUMBER_DISCOVERY by sort_order - "port my number"
        # must not classify as a generic number question.
        "keywords": [
            "port", "porting", "port my number", "transfer my number", "keep my number",
            "move my number", "switch my number", "existing number over", "lnp",
        ],
        "sort_order": 5,
        "is_quick_start": False,
    },
    {
        "intent_id": "EMERGENCY_INFO",
        "title": "Emergency calling",
        "description": "How emergency services relate to internet-based numbers.",
        "keywords": [
            "emergency", "911", "112", "999", "e911", "emergency services",
            "emergency calling",
        ],
        "sort_order": 6,
        "is_quick_start": False,
    },
    {
        "intent_id": "PRIVACY_INFO",
        "title": "Privacy & data",
        "description": "What the assistant stores and your privacy options.",
        "keywords": [
            "privacy", "gdpr", "ccpa", "personal data", "delete my data",
            "data protection", "my information", "anonymous",
        ],
        "sort_order": 7,
        "is_quick_start": False,
    },
    {
        "intent_id": "RECEPTIONIST_EXAMPLE",
        "title": "AI Receptionist example",
        "description": "A fictional example preview of a receptionist call.",
        # Checked BEFORE RECEPTIONIST_OVERVIEW by sort_order.
        "keywords": [
            "example", "sample", "demo", "what does it sound like", "hear it",
            "show me an example", "hear an example",
        ],
        "sort_order": 15,
        "is_quick_start": False,
    },
]

# WF-11 source registry + claim bodies. intent_id ties a row to the intent
# whose answer it grounds; authority is A3 (current official product
# content) for every seeded row - nothing A4 (unattributed) may exist,
# and A0 (live provider facts) is deliberately never seeded because the
# assistant never claims live inventory (WF-02's inventory plane).
KNOWLEDGE_SOURCES = [
    {
        "slug": "get-local-number-product-page",
        "title": "Get a Local Number - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/get-local-number",
        "authority_class": "a3_product_content",
        "intent_id": "NUMBER_DISCOVERY",
        "body": (
            "Zoiko Local offers local phone numbers in the countries it operates in. "
            "You can browse current countries, number types (such as local and toll-free), "
            "and indicative rental costs from the Get a Local Number page before creating "
            "an account."
        ),
    },
    {
        "slug": "global-coverage-page",
        "title": "Global Coverage - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/global-coverage",
        "authority_class": "a3_product_content",
        "intent_id": "NUMBER_DISCOVERY",
        "body": (
            "The countries where Zoiko Local currently operates, and each country's "
            "number types and verification requirements, are listed on the Global "
            "Coverage page - coverage changes over time, so it's always worth checking "
            "there first."
        ),
    },
    {
        "slug": "verification-help-overview",
        "title": "Verification requirements - Zoiko Local Help",
        "canonical_url": "https://zoikolocal.com/get-local-number",
        "authority_class": "a3_product_content",
        "intent_id": "NUMBER_DISCOVERY",
        "body": (
            "Some countries and number types require identity or address verification "
            "before a number can be activated. Which checks apply is confirmed during "
            "signup and checkout for your specific selection - the assistant cannot "
            "confirm live availability or final eligibility in chat."
        ),
    },
    {
        "slug": "ai-receptionist-product-page",
        "title": "AI Receptionist - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/ai-receptionist",
        "authority_class": "a3_product_content",
        "intent_id": "RECEPTIONIST_OVERVIEW",
        "body": (
            "The AI Receptionist is a per-number feature that answers calls to your "
            "Zoiko Local number, greets callers, asks what they need, takes messages, "
            "and can escalate urgent calls to a team member you nominate."
        ),
    },
    {
        "slug": "ai-receptionist-setup-help",
        "title": "AI Receptionist setup - Zoiko Local Help",
        "canonical_url": "https://zoikolocal.com/ai-receptionist",
        "authority_class": "a3_product_content",
        "intent_id": "RECEPTIONIST_OVERVIEW",
        "body": (
            "The receptionist is enabled and configured from your account dashboard "
            "after you sign up. This assistant can explain how it works and show a "
            "fictional example, but it cannot set up, test, or change your receptionist."
        ),
    },
    {
        "slug": "ai-receptionist-example-script",
        "title": "AI Receptionist example preview - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/ai-receptionist",
        "authority_class": "a3_product_content",
        "intent_id": "RECEPTIONIST_EXAMPLE",
        "body": (
            "Example only - a fictional business and call, not a real customer: "
            "\u201cThanks for calling Rivera Dental, this is the assistant. How can I "
            "help you today?\u201d The caller says they want to book a cleaning; the "
            "assistant can capture the reason, offer common answers from the "
            "business's own configuration, take a message, or pass the call to a "
            "person if it's urgent. Actual behavior depends entirely on how the "
            "business configures it."
        ),
    },
    {
        "slug": "cloud-calling-product-page",
        "title": "Cloud Calling - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/calling",
        "authority_class": "a3_product_content",
        "intent_id": "CALLING_OVERVIEW",
        "body": (
            "Zoiko Local includes cloud calling: make and receive calls on your local "
            "number from the browser-based dashboard or the Zoiko Local app, with "
            "call flows, voicemail, and optional recording."
        ),
    },
    {
        "slug": "calling-considerations-help",
        "title": "Calling considerations - Zoiko Local Help",
        "canonical_url": "https://zoikolocal.com/calling",
        "authority_class": "a3_product_content",
        "intent_id": "CALLING_OVERVIEW",
        "body": (
            "This assistant can walk you through how calling works and what to think "
            "about before you buy, read-only. It cannot place calls, configure call "
            "flows, or quote your exact call costs - those are set up and shown in "
            "your account dashboard after signup."
        ),
    },
    {
        "slug": "pricing-product-page",
        "title": "Plans & Pricing - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/pricing",
        "authority_class": "a3_product_content",
        "intent_id": "PLANS_OVERVIEW",
        "body": (
            "Plan prices shown by this assistant come from Zoiko Local's approved "
            "price catalog. Your final total also depends on number rental and "
            "usage, and is calculated during secure checkout - the assistant never "
            "quotes a custom total."
        ),
    },
    {
        "slug": "activation-help-overview",
        "title": "Getting started - Zoiko Local Help",
        "canonical_url": "https://zoikolocal.com/start-free",
        "authority_class": "a3_product_content",
        "intent_id": "ACTIVATION_GUIDE",
        "body": (
            "Getting started: create a free Zoiko Local account, pick your country "
            "and local number, complete any verification that country requires, and "
            "the number activates once the checks pass. No card is needed to explore."
        ),
    },
    {
        "slug": "first-call-readiness-help",
        "title": "First-call readiness - Zoiko Local Help",
        "canonical_url": "https://zoikolocal.com/calling",
        "authority_class": "a3_product_content",
        "intent_id": "ACTIVATION_GUIDE",
        "body": (
            "After activation, test an inbound and an outbound call, set up voicemail "
            "and call flows, and optionally enable the AI Receptionist. If something "
            "isn't working, the readiness checklist in your dashboard and our support "
            "team can help - this assistant cannot change account settings."
        ),
    },
    {
        "slug": "porting-product-page",
        "title": "Number Porting - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/number-porting",
        "authority_class": "a3_product_content",
        "intent_id": "PORTING_GUIDE",
        "body": (
            "You may be able to bring an existing number to Zoiko Local. Porting "
            "depends on the country, your current carrier, and their release "
            "process - start from the Number Porting page."
        ),
    },
    {
        "slug": "porting-authorization-help",
        "title": "Porting authorization - Zoiko Local Help",
        "canonical_url": "https://zoikolocal.com/number-porting",
        "authority_class": "a3_product_content",
        "intent_id": "PORTING_GUIDE",
        "body": (
            "A port requires authorization evidence and account details held with "
            "your current carrier. This assistant cannot submit a port, share or "
            "collect those documents, or guarantee a completion date - please don't "
            "send full phone numbers or carrier account details in chat."
        ),
    },
    {
        "slug": "emergency-calling-product-page",
        "title": "Emergency Calling - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/emergency-calling",
        "authority_class": "a3_product_content",
        "intent_id": "EMERGENCY_INFO",
        "body": (
            "Zoiko Local numbers are internet-based and are not a replacement for "
            "emergency calling. If you are in danger, contact your local emergency "
            "number (such as 911, 112, or 999) directly. The Emergency Calling page "
            "explains what to consider before relying on any internet-based number."
        ),
    },
    {
        "slug": "video-product-page",
        "title": "Video - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/video",
        "authority_class": "a3_product_content",
        "intent_id": "CALLING_OVERVIEW",
        "body": (
            "Zoiko Local provides video meetings in the same workspace as numbers and "
            "calling. Video availability and session capabilities can depend on the plan "
            "and workspace configuration."
        ),
    },
    {
        "slug": "privacy-policy-page",
        "title": "Zoiko Local Privacy Policy",
        "canonical_url": "https://zoikolocal.com/zoiko-local-privacy-policy",
        "authority_class": "a3_product_content",
        "intent_id": "PRIVACY_INFO",
        "body": (
            "This assistant keeps your conversation in your browser for the session, "
            "and you can clear the chat view or end the session at any time. Please "
            "don't share sensitive details like card numbers, passwords, or one-time "
            "codes in chat. The privacy policy explains what Zoiko Local collects."
        ),
    },
    {
        "slug": "contact-support-page",
        "title": "Contact Support - Zoiko Local",
        "canonical_url": "https://zoikolocal.com/contact-support",
        "authority_class": "a3_product_content",
        "intent_id": "CONTACT_HANDOFF",
        "body": (
            "The official Contact Support page is the route for account-specific help. "
            "The assistant can answer guided questions and link to that page, but it "
            "cannot open, update, or resolve a support case."
        ),
    },
]

# WF-13 urgent-path patterns. First-person distress phrasing - deliberately
# NOT a bare "emergency" keyword (that's EMERGENCY_INFO's informational
# route). These outrank everything: any hit returns the static safety
# script before any other processing, so the urgent path can never be
# routed away by intent classification, LLM behavior, or a flow state.
SAFETY_RULES = [
    # URGENT - WF-13
    {"category": "urgent", "pattern": r"\b(call|dial|need|phone|ring|get me)\s+(911|112|999|emergency(\s+services)?)\b", "sort_order": 1},
    {"category": "urgent", "pattern": r"\b(having|in|there'?s)\s+(an?\s+)?emergency\b", "sort_order": 2},
    {"category": "urgent", "pattern": r"\b(heart attack|stroke|seizure|unconscious|not breathing|choking|bleeding out|dying)\b", "sort_order": 3},
    {"category": "urgent", "pattern": r"\b(suicid\w*|overdos\w*|self[- ]harm)\b", "sort_order": 4},
    {"category": "urgent", "pattern": r"\b(intruder|attacker|being (attacked|robbed|followed)|break[ -]?in)\b", "sort_order": 5},
    {"category": "urgent", "pattern": r"\b(gas leak|house fire|on fire|flood(ed)?)\b", "sort_order": 6},
    {"category": "urgent", "pattern": r"\bhelp me now\b", "sort_order": 7},
    # SENSITIVE - WF-10 secret redaction before any processing/storage
    {"category": "sensitive", "pattern": r"\b(?:\d[ -]*?){13,16}\b", "sort_order": 1},
    {"category": "sensitive", "pattern": r"\b(card|credit card|debit card)\s+(number|details|info|information)\b", "sort_order": 2},
    {"category": "sensitive", "pattern": r"\b(cvv|cvc|security code)\b", "sort_order": 3},
    {"category": "sensitive", "pattern": r"\b(password|passcode|pin)\s+(is|:)\s*\S+", "sort_order": 4},
    {"category": "sensitive", "pattern": r"\b(otp|one[- ]time (code|password)|verification code)\s+(is|:)\s*\S+", "sort_order": 5},
    {"category": "sensitive", "pattern": r"\b\d{4}\s*[- ]?\s*\d{4}\s*[- ]?\s*\d{4}\s*[- ]?\s*\d{4}\b", "sort_order": 6},
]

# WF-03/WF-01 destination allowlist - keys are the contract the API
# speaks; URLs match real routes in zoiko-local-react's app/ directory.
DESTINATIONS = [
    {"destination_key": "GET_LOCAL_NUMBER", "label": "Get a Local Number", "url": "/get-local-number"},
    {"destination_key": "CLOUD_CALLING", "label": "Cloud Calling", "url": "/calling"},
    {"destination_key": "AI_RECEPTIONIST", "label": "AI Receptionist", "url": "/ai-receptionist"},
    {"destination_key": "VIDEO", "label": "Video Meetings", "url": "/video"},
    {"destination_key": "PRICING", "label": "Plans & Pricing", "url": "/pricing"},
    {"destination_key": "PORTING", "label": "Number Porting", "url": "/number-porting"},
    {"destination_key": "EMERGENCY_CALLING", "label": "Emergency Calling", "url": "/emergency-calling"},
    {"destination_key": "START_FREE", "label": "Start Free", "url": "/start-free"},
    {"destination_key": "CONTACT_SALES", "label": "Talk to Sales", "url": "/contact-sales"},
    {"destination_key": "CONTACT_SUPPORT", "label": "Contact Support", "url": "/contact-support"},
    {"destination_key": "PRIVACY_POLICY", "label": "Privacy Policy", "url": "/zoiko-local-privacy-policy"},
    {"destination_key": "GLOBAL_COVERAGE", "label": "Global Coverage", "url": "/global-coverage"},
]

# WF-13 static safety bundle - the urgent-path response copy. Deterministic
# by requirement ("must never be paraphrased, regenerated or summarized by
# a language model"); defined here as data so the UI and API share one
# string, and kept out of the knowledge-source registry because it is a
# safety script, not a citable claim.
URGENT_RESPONSE_TEXT = (
    "If you are in immediate danger, please stop and contact your local "
    "emergency number (such as 911, 112, or 999) right away. I cannot call "
    "emergency services or connect you to anyone, and Zoiko Local numbers "
    "are not a replacement for emergency calling."
)

SENSITIVE_RESPONSE_TEXT = (
    "Please don't share sensitive details like card numbers, passwords, or "
    "one-time codes in chat - I don't need them to help, and I've discarded "
    "that message. Ask me about local numbers, calling, the AI Receptionist, "
    "plans, or getting started instead."
)
