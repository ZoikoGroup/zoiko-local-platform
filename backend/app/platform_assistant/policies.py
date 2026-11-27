import re

from app.platform_assistant import content

_MUTATION_PATTERNS = [
    r"\b(buy|purchase|reserve|order)\b.{0,40}\b(number|numbers|plan|addon|add-on)\b",
    r"\b(port|porting|submit|start|begin)\b.{0,40}\b(request|port|carrier|number)\b",
    r"\b(cancel|terminate|suspend|disconnect)\b.{0,40}\b(plan|subscription|number|service|account|port)\b",
    r"\b(refund|chargeback|pay|payment|invoice)\b.{0,40}\b(now|issue|create|make|process|change|update)\b",
    r"\b(change|switch|upgrade|downgrade|modify|edit|update)\b.{0,40}\b(plan|payment|billing|number|setting|settings|route|routing|forwarding)\b",
    r"\b(send|reply|call|dial|text|message|email|invite)\b.{0,40}\b(now|for me|on my behalf|to [^\s]+)\b",
    r"\b(publish|deploy|enable|disable|toggle|record|capture|start|stop|delete|remove|purge|erase)\b.{0,40}\b(receptionist|recording|transcription|capture|video|room|retention|data|policy|policies)\b",
    r"\b(grant|revoke|give|remove)\b.{0,40}\b(role|access|permission|admin|owner)\b",
    r"\b(run|execute|start|trigger)\b.{0,40}\b(diagnostic|diagnostics|test|sweep|purge)\b",
    r"\b(open|create|raise|file)\b.{0,40}\b(ticket|case|support case|incident|dispute)\b",
]

_SENSITIVE_PATTERNS = [
    r"\b(?:sk|pk|api|secret|token|password|passcode|otp|pin)\b[\w-]{8,}",
    r"\+?\d[\d\s().-]{8,}\d",
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",
]

_COMPILED_MUTATIONS = [re.compile(p, re.IGNORECASE) for p in _MUTATION_PATTERNS]
_COMPILED_SENSITIVE = [re.compile(p, re.IGNORECASE) for p in _SENSITIVE_PATTERNS]
_QUESTION_STARTER = re.compile(r"^(how|where|what|when|why|can|could|would|should|do|does|is|are|which)\b", re.IGNORECASE)
_EXPLICIT_ACTION = re.compile(r"\b(please|now|for me|on my behalf)\b", re.IGNORECASE)


def classify_request(text: str) -> dict:
    value = text or ""
    question = value.strip().endswith("?") or bool(_QUESTION_STARTER.match(value.strip()))
    explicit_action = bool(_EXPLICIT_ACTION.search(value))
    mutation_detected = any(pattern.search(value) for pattern in _COMPILED_MUTATIONS) and not (question and not explicit_action)
    sensitive_detected = any(pattern.search(value) for pattern in _COMPILED_SENSITIVE)
    topic = detect_topic(value)
    return {
        "mutation_detected": mutation_detected,
        "sensitive_detected": sensitive_detected,
        "topic": topic,
        "risk_tier": _risk_tier(mutation_detected, topic),
    }


def detect_topic(text: str) -> str | None:
    value = (text or "").lower()
    for topic, keywords in content.TOOL_TOPICS.items():
        if any(keyword in value for keyword in keywords):
            return topic
    return None


def _risk_tier(mutation_detected: bool, topic: str | None) -> str:
    if not mutation_detected:
        return "R1" if topic else "R0"
    if topic in {"billing", "porting", "numbers"}:
        return "R4"
    if topic in {"compliance", "support"}:
        return "R5"
    return "R3"


def destination_key_for_topic(topic: str | None, module: str) -> str:
    if topic and topic in content.MODULES:
        return content.MODULES[topic]["destination_key"]
    if module in content.MODULES:
        return content.MODULES[module]["destination_key"]
    return "DASHBOARD_HOME"


def is_greeting(text: str) -> bool:
    value = (text or "").strip().lower()
    return bool(re.fullmatch(r"(hi|hello|hey|good (morning|afternoon|evening)|greetings)[.! ]*", value))


def match_general_faq(text: str) -> dict | None:
    value = text or ""
    for entry in content.GENERAL_FAQ:
        if any(re.search(pattern, value, re.IGNORECASE) for pattern in entry["patterns"]):
            return entry
    return None
