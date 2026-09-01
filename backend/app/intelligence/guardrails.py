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

Patterns are data, not hardcoded Python constants (per the architecture
rule: "Compliance rules are stored as data (a table), never hardcoded
if-statements") - see intelligence.models.GuardrailRule and
load_active_guardrail_patterns below. The categories these patterns
populate are pricing_commitment/legal_advice/medical_advice - the same
three this module has always checked; only their storage moved.
"""

import re

from sqlalchemy.orm import Session

from app.intelligence.models import GuardrailRule

# Stable output order for the three categories this guardrail has always
# checked (pricing, legal, medical) - callers/tests rely on this order. Any
# additional category seeded into GuardrailRule later (e.g. "financial")
# still gets flagged, just appended after these three in first-seen order.
_CATEGORY_ORDER = ["pricing_commitment", "legal_advice", "medical_advice"]


def load_active_guardrail_patterns(db: Session) -> dict[str, list[re.Pattern]]:
    """Loads every active GuardrailRule row and compiles it, grouped by
    category - the data-driven replacement for this module's old hardcoded
    _PRICING_PATTERNS/_LEGAL_PATTERNS/_MEDICAL_PATTERNS constants. Compiled
    fresh on every call (this only runs on the receptionist's
    qualify-caller path, not a request hot loop) so a staff edit to a rule
    takes effect immediately, with no cache to invalidate. A row with an
    invalid regex is skipped rather than raising - one bad pattern
    shouldn't take down the whole guardrail check on a live call.
    """
    rules = db.query(GuardrailRule).filter(GuardrailRule.is_active.is_(True)).all()
    patterns: dict[str, list[re.Pattern]] = {}
    for rule in rules:
        try:
            compiled = re.compile(rule.pattern, re.IGNORECASE)
        except re.error:
            continue
        patterns.setdefault(rule.category, []).append(compiled)
    return patterns


def check_for_disallowed_commitments(db: Session, *texts: str | None) -> list[str]:
    """Returns the disallowed-commitment categories found in the given
    AI-generated texts (the receptionist's structured summary/reason plus
    every other LLM-extracted field surfaced to staff - spam_reason,
    callback_preference, caller name, caller company - since all of them
    come from the same untrusted LLM call), empty if none. Order is stable
    (pricing, legal, medical, then any other seeded category) so callers
    and tests can rely on it.
    """
    combined = " ".join(t for t in texts if t)
    if not combined:
        return []
    category_patterns = load_active_guardrail_patterns(db)
    ordered_categories = _CATEGORY_ORDER + [c for c in category_patterns if c not in _CATEGORY_ORDER]
    return [
        category
        for category in ordered_categories
        if category in category_patterns and any(p.search(combined) for p in category_patterns[category])
    ]
