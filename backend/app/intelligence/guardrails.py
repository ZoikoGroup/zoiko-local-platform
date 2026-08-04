"""
AI Receptionist guardrail (Roadmap §7: "no pricing/legal/medical
commitments"). The system prompt in app/integrations/llm/groq.py already
instructs the model never to make these commitments - that's necessary but
not sufficient, since nothing previously checked whether it actually
complied. This module is that check: a deterministic, explainable scan of
the model's OWN generated output (never the caller's raw transcript - a
caller mentioning a price or a lawsuit is normal and not something to flag)
for language that reads as a firm commitment in a disallowed category.

Deterministic and regex-based on purpose, not a second LLM call - an LLM
judging an LLM's output would just need its own guardrail, and a fixed set
of patterns is auditable in a way a second model call isn't.
"""

import re

_PRICING_PATTERNS = [
    re.compile(r"\$\s?\d"),
    re.compile(r"\b\d+(\.\d+)?\s?(dollars|usd|percent|% ?(off|discount))\b", re.IGNORECASE),
    re.compile(r"\b(guarantee|guaranteed|promise[ds]?)\b[^.]{0,40}\b(price|cost|rate|discount|refund|quote)\b", re.IGNORECASE),
    re.compile(r"\b(price|cost|rate|quote) (is|will be|of) \b", re.IGNORECASE),
    re.compile(r"\b(free|no charge|complimentary)\b[^.]{0,40}\b(service|repair|installation|replacement)\b", re.IGNORECASE),
]

_LEGAL_PATTERNS = [
    re.compile(r"\blegal advice\b", re.IGNORECASE),
    re.compile(r"\b(legally (binding|obligated|required|entitled))\b", re.IGNORECASE),
    re.compile(r"\byou (will|can|should) (sue|win (your|the) case|be liable)\b", re.IGNORECASE),
    re.compile(r"\bwe (accept|admit) (liability|fault)\b", re.IGNORECASE),
]

_MEDICAL_PATTERNS = [
    re.compile(r"\bmedical advice\b", re.IGNORECASE),
    re.compile(r"\byou (have|are experiencing) (a|an) [\w\s]{0,30}(condition|disease|infection|disorder)\b", re.IGNORECASE),
    re.compile(r"\b(diagnos(e|is|ed|ing)|prescri(be|bed|ption))\b", re.IGNORECASE),
]

_CATEGORY_PATTERNS: dict[str, list[re.Pattern]] = {
    "pricing_commitment": _PRICING_PATTERNS,
    "legal_advice": _LEGAL_PATTERNS,
    "medical_advice": _MEDICAL_PATTERNS,
}


def check_for_disallowed_commitments(*texts: str | None) -> list[str]:
    """Returns the disallowed-commitment categories found in the given
    AI-generated texts (e.g. the receptionist's structured summary/reason),
    empty if none. Order is stable (pricing, legal, medical) so callers and
    tests can rely on it."""
    combined = " ".join(t for t in texts if t)
    if not combined:
        return []
    return [category for category, patterns in _CATEGORY_PATTERNS.items() if any(p.search(combined) for p in patterns)]
