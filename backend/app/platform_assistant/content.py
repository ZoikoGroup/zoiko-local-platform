CONTENT_VERSION = "pa-phase1-2026-11-19"

ASSISTANT_NAME = "Zoiko Local Assistant"
AI_DISCLOSURE = "I'm an automated Zoiko Local assistant. Read-only."
GREETING_RESPONSE = (
    "Hi! I'm the Zoiko Local Assistant. I can explain your workspace, summarize "
    "records you're allowed to view, and point you to the right secure screen. "
    "What would you like to know?"
)
READ_ONLY_NOTICE = "I can explain your setup and summarize information you're allowed to view. Changes happen in the secure workspace."
GENERAL_FAQ = [
    {
        "key": "about",
        "patterns": [r"\b(what is|tell me about|about)\s+zoiko(\s+local)?\b", r"\bwho are you\b"],
        "answer": (
            "Zoiko Local is your authenticated workspace for local numbers, calling, call "
            "flows, the AI Receptionist, messaging and voicemail, video, porting, analytics, "
            "billing, compliance, and support. I can explain what is there and open the right "
            "screen; changes stay in the secure workspace."
        ),
        "evidence": ["numbering.countries", "media.numbers"],
        "actions": ["DASHBOARD_HOME"],
    },
    {
        "key": "capabilities",
        "patterns": [r"\bwhat can you do\b", r"\bhow can you help\b", r"\bwhat do you (do|offer|support)\b"],
        "answer": (
            "I can summarize workspace records you are permitted to view: numbers, recent calls, "
            "receptionist activity, porting requests, plan entitlements, usage, inbox counts, "
            "analytics, video sessions, consent posture, incidents, and supported countries. "
            "I cannot make changes, place calls, send messages, join meetings, take payments, or "
            "confirm live number availability."
        ),
        "evidence": ["media.numbers", "billing.entitlements", "ops.incidents"],
        "actions": ["DASHBOARD_HOME"],
    },
    {
        "key": "data_sources",
        "patterns": [r"\b(where does|what) (your|this) (data|information) (come from|use)\b", r"\bare you reading my data\b"],
        "answer": (
            "I use only authenticated workspace data and approved registries available to your "
            "account, and I re-check permissions before each protected read. I do not show "
            "message content or raw personal details here."
        ),
        "evidence": ["billing.entitlements", "consent.status"],
        "actions": ["DASHBOARD_HOME"],
    },
    {
        "key": "get_number",
        "patterns": [r"\bhow do i (get|buy|purchase|acquire|find)\b.{0,30}\b(number|numbers)\b", r"\bwhere (do|can) i (get|buy|manage) (a\s+)?(local\s+)?numbers?\b"],
        "answer": (
            "Use the Numbers area to search, acquire, review, and assign local numbers. I can "
            "open it for you; availability and purchase are confirmed in the secure flow."
        ),
        "evidence": ["numbering.countries", "media.numbers"],
        "actions": ["DASHBOARD_NUMBERS"],
    },
    {
        "key": "porting_location",
        "patterns": [r"\bwhere (do|can) i (track|see|find|manage)\b.{0,30}\bport(ing|s|ed)?\b", r"\bhow do i track (a|my) port\b"],
        "answer": (
            "Porting requests are tracked from the Numbers area. I can summarize status you are "
            "allowed to see; submitting or changing a port stays in the secure workflow."
        ),
        "evidence": ["porting.requests"],
        "actions": ["DASHBOARD_NUMBERS"],
    },
    {
        "key": "billing_help",
        "patterns": [r"\bwhere (are|do i find|can i see) (my\s+)?(invoices?|billing|plan|usage)\b"],
        "answer": (
            "Plan entitlements, metered usage, and billing history are in the Billing area. "
            "I can open it; amounts and invoices stay in the secure screen."
        ),
        "evidence": ["billing.entitlements", "billing.usage", "billing.history"],
        "actions": ["DASHBOARD_BILLING"],
    },
    {
        "key": "calls_location",
        "patterns": [r"\bwhere (are|do i find|can i see)\b.{0,30}\b(calls?|call history|call recaps?|recordings?)\b"],
        "answer": (
            "Recent calls, recaps, and recordings you are permitted to view are in the Calls "
            "area. I can open it or summarize the call list; playback and settings stay in the "
            "secure screen."
        ),
        "evidence": ["media.calls"],
        "actions": ["DASHBOARD_CALLS"],
    },
    {
        "key": "receptionist_location",
        "patterns": [r"\bwhere (do|can) i (manage|configure|find|see)\b.{0,30}\b(receptionist|call flows?)\b", r"\bwhat is (the|your) (ai\s+)?receptionist\b", r"\bhow (does|do i|can i)\b.{0,30}\breceptionist\b"],
        "answer": (
            "AI Receptionist answers and routing are configured in Call Flows. I can summarize "
            "receptionist activity and open that area; enabling or editing it stays in the "
            "secure workspace."
        ),
        "evidence": ["media.receptionist"],
        "actions": ["DASHBOARD_CALL_FLOWS"],
    },
    {
        "key": "inbox_location",
        "patterns": [r"\bwhere (are|do i find|can i see)\b.{0,30}\b(messages?|conversations?|voicemails?|inbox)\b"],
        "answer": (
            "Conversations and voicemail are in the Messaging and Voicemail areas. I can show "
            "counts or open the right screen; message content and sending stay in the secure "
            "workspace."
        ),
        "evidence": ["messaging.conversations", "media.voicemail"],
        "actions": ["DASHBOARD_MESSAGING", "DASHBOARD_VOICEMAIL"],
    },
    {
        "key": "analytics_location",
        "patterns": [r"\bwhere (are|do i find|can i see)\b.{0,30}\b(reports?|analytics|insights?)\b"],
        "answer": (
            "Operational reports and quality insights are in Analytics and Reports. I can open "
            "those screens and summarize permitted overview metrics; I do not expose raw "
            "transcripts or personal details here."
        ),
        "evidence": ["analytics.overview"],
        "actions": ["DASHBOARD_REPORTS"],
    },
    {
        "key": "video_location",
        "patterns": [r"\bwhere (are|do i find|can i see)\b.{0,30}\b(video|meetings?|rooms?)\b", r"\bwhat video sessions exist\b"],
        "answer": (
            "Video rooms and sessions are in the Video area. I can summarize permitted session "
            "metadata and open it; starting, joining, or recording a meeting stays in the "
            "secure workspace."
        ),
        "evidence": ["media.video"],
        "actions": ["DASHBOARD_VIDEO"],
    },
    {
        "key": "compliance_location",
        "patterns": [r"\bwhere (are|do i find|can i see)\b.{0,30}\b(consent|retention|compliance|data requests?)\b"],
        "answer": (
            "Consent posture, retention policies, and compliance guidance are in the Compliance "
            "area. I can summarize what your permissions allow; policy changes and data requests "
            "stay in the secure workflow."
        ),
        "evidence": ["consent.status", "retention.policies"],
        "actions": ["DASHBOARD_COMPLIANCE"],
    },
    {
        "key": "support",
        "patterns": [r"\b(where do i get|how do i get|need)\s+help\b", r"\bcontact support\b", r"\bwhere (are|do i find|can i see)\b.{0,30}\b(incidents?|status|outages?)\b"],
        "answer": (
            "The Support area shows current incidents and the secure help route. I can open it "
            "for you; opening or updating a case stays in the secure workflow."
        ),
        "evidence": ["ops.incidents", "ops.synthetic"],
        "actions": ["DASHBOARD_SUPPORT"],
    },
]
MUTATION_FALLBACK = "I can help you find that setting, but I can't make the change from this chat."
PERMISSION_FALLBACK = "I can't access that information from your current workspace permissions."
UNKNOWN_FALLBACK = "I can't verify that from an available source right now."
SESSION_ENDED_COPY = "Your session has ended. Sign in again to continue safely."
CONTEXT_CHANGED_COPY = "You switched workspaces. Start a new assistant session to keep information separate."
SUMMARY_CAVEAT = "AI summaries can be incomplete. Confirm operational details in the linked source."

MODULES = {
    "overview": {
        "title": "Workspace overview",
        "description": "Read-only guidance across your authenticated Zoiko Local workspace.",
        "prompts": ["What can you see?", "Where do I manage numbers?"],
        "destination_key": "DASHBOARD_HOME",
    },
    "calls": {
        "title": "Calls",
        "description": "Call history and recaps you are allowed to view.",
        "prompts": ["Summarize recent calls", "Where are AI recaps?"],
        "destination_key": "DASHBOARD_CALLS",
    },
    "receptionist": {
        "title": "AI Receptionist",
        "description": "Receptionist activity and Call Flows configuration guidance.",
        "prompts": ["How did the receptionist handle calls?", "Where are call flows?"],
        "destination_key": "DASHBOARD_CALL_FLOWS",
    },
    "numbers": {
        "title": "Numbers",
        "description": "Number portfolio, acquisition, assignment, and porting guidance.",
        "prompts": ["Which numbers are on my account?", "Where do I manage numbers?"],
        "destination_key": "DASHBOARD_NUMBERS",
    },
    "porting": {
        "title": "Porting",
        "description": "Porting request status and next steps.",
        "prompts": ["What porting requests exist?", "Where do I track a port?"],
        "destination_key": "DASHBOARD_NUMBERS",
    },
    "billing": {
        "title": "Billing",
        "description": "Entitlements, usage, and invoice explanation.",
        "prompts": ["What is included in my plan?", "Where are invoices?"],
        "destination_key": "DASHBOARD_BILLING",
    },
    "inbox": {
        "title": "Inbox",
        "description": "Conversations and voicemail guidance.",
        "prompts": ["Do I have new voicemails?", "Where is the shared inbox?"],
        "destination_key": "DASHBOARD_MESSAGING",
    },
    "analytics": {
        "title": "Analytics",
        "description": "Permissioned operational overview, reports, and quality insight.",
        "prompts": ["Give me an operations overview", "Where are reports?"],
        "destination_key": "DASHBOARD_REPORTS",
    },
    "acquisition": {
        "title": "Number acquisition",
        "description": "Supported-country registry and secure acquisition guidance.",
        "prompts": ["Which countries are supported?", "How do I find a new number?"],
        "destination_key": "DASHBOARD_NUMBERS",
    },
    "video": {
        "title": "Video",
        "description": "Video room and session metadata you are allowed to view.",
        "prompts": ["What video sessions exist?", "Where are video meetings?"],
        "destination_key": "DASHBOARD_VIDEO",
    },
    "compliance": {
        "title": "Compliance",
        "description": "Consent and retention posture you are allowed to view; no policy changes in chat.",
        "prompts": ["What is my consent status?", "Where are retention policies?"],
        "destination_key": "DASHBOARD_COMPLIANCE",
    },
    "support": {
        "title": "Support and status",
        "description": "Current incidents and support navigation.",
        "prompts": ["Are there active incidents?", "Where do I get help?"],
        "destination_key": "DASHBOARD_SUPPORT",
    },
}

DESTINATIONS = {
    "DASHBOARD_HOME": {"label": "Open dashboard", "href": "/dashboard"},
    "DASHBOARD_CALLS": {"label": "Open calls", "href": "/dashboard/calls"},
    "DASHBOARD_CALL_FLOWS": {"label": "Open call flows", "href": "/dashboard/call-flows"},
    "DASHBOARD_NUMBERS": {"label": "Open numbers", "href": "/dashboard/numbers"},
    "DASHBOARD_BILLING": {"label": "Open billing", "href": "/dashboard/billing"},
    "DASHBOARD_MESSAGING": {"label": "Open messaging", "href": "/dashboard/messaging"},
    "DASHBOARD_REPORTS": {"label": "Open reports", "href": "/dashboard/reports"},
    "DASHBOARD_VIDEO": {"label": "Open video", "href": "/dashboard/video"},
    "DASHBOARD_COMPLIANCE": {"label": "Open compliance", "href": "/dashboard/compliance"},
    "DASHBOARD_SUPPORT": {"label": "Open support", "href": "/dashboard/support"},
    "DASHBOARD_VOICEMAIL": {"label": "Open voicemail", "href": "/dashboard/voicemail"},
}

CONTENT_ROWS = [
    {
        "key": "ai_disclosure",
        "title": "AI identity",
        "category": "copy",
        "body": AI_DISCLOSURE,
    },
    {
        "key": "read_only_notice",
        "title": "Read-only notice",
        "category": "copy",
        "body": READ_ONLY_NOTICE,
    },
    {
        "key": "mutation_fallback",
        "title": "Mutation fallback",
        "category": "copy",
        "body": MUTATION_FALLBACK,
    },
    {
        "key": "permission_fallback",
        "title": "Permission fallback",
        "category": "copy",
        "body": PERMISSION_FALLBACK,
    },
    {
        "key": "unknown_fallback",
        "title": "Unknown fallback",
        "category": "copy",
        "body": UNKNOWN_FALLBACK,
    },
    {
        "key": "session_ended_copy",
        "title": "Session ended",
        "category": "copy",
        "body": SESSION_ENDED_COPY,
    },
    {
        "key": "context_changed_copy",
        "title": "Context changed",
        "category": "copy",
        "body": CONTEXT_CHANGED_COPY,
    },
    {
        "key": "summary_caveat",
        "title": "Accuracy caveat",
        "category": "copy",
        "body": SUMMARY_CAVEAT,
    },
]

EVIDENCE_SOURCES = {
    "media.calls": "Call records",
    "media.voicemail": "Voicemail records",
    "media.receptionist": "AI Receptionist call records",
    "media.numbers": "Number portfolio",
    "porting.requests": "Porting requests",
    "billing.entitlements": "Entitlement snapshot",
    "billing.usage": "Usage summary",
    "billing.history": "Billing history",
    "messaging.conversations": "Message conversations",
    "analytics.overview": "Operational analytics",
    "numbering.countries": "Supported countries",
    "media.video": "Video sessions",
    "consent.status": "Consent records",
    "retention.policies": "Retention policies",
    "ops.incidents": "Public incident history",
    "ops.synthetic": "Synthetic checks",
}

TOOL_TOPICS = {
    "calls": {"calls", "call", "recap", "recaps", "summary", "summaries", "history", "recording", "recordings"},
    "receptionist": {"receptionist", "ai receptionist", "greeting", "routing", "call flow", "call flows", "forwarding"},
    "numbers": {"number", "numbers", "portfolio", "assignment", "assignments", "line", "lines"},
    "porting": {"port", "porting", "carrier", "activation", "track"},
    "billing": {"bill", "billing", "invoice", "invoices", "usage", "plan", "entitlement", "entitlements"},
    "inbox": {"inbox", "message", "messages", "conversation", "conversations", "voicemail", "voicemails", "sms", "text"},
    "analytics": {"analytics", "report", "reports", "insight", "insights", "quality", "operations", "metrics", "dashboard"},
    "acquisition": {"country", "countries", "availability", "eligibility", "coverage", "acquire", "acquisition"},
    "video": {"video", "meeting", "meetings", "room", "rooms", "join", "guest"},
    "compliance": {"consent", "retention", "compliance", "legal hold", "privacy", "data request", "data requests"},
    "support": {"support", "incident", "incidents", "status", "outage", "health", "help"},
}
