import pytest

from app.intelligence.guardrails import check_for_disallowed_commitments
from app.intelligence.models import GuardrailRule

# Mirrors the exact patterns that used to be the hardcoded _PRICING_PATTERNS/
# _LEGAL_PATTERNS/_MEDICAL_PATTERNS constants in app.intelligence.guardrails,
# before they moved to being GuardrailRule seed data (per the architecture
# rule: compliance rules are stored as data, never hardcoded if-statements).
_SEED_PATTERNS = [
    ("pricing_commitment", r"\$\s?\d"),
    ("pricing_commitment", r"\b\d+(\.\d+)?\s?(dollars|usd|percent|% ?(off|discount))\b"),
    ("pricing_commitment", r"\b(guarantee|guaranteed|promise[ds]?)\b[^.]{0,40}\b(price|cost|rate|discount|refund|quote)\b"),
    ("pricing_commitment", r"\b(price|cost|rate|quote) (is|will be|of) \b"),
    ("pricing_commitment", r"\b(free|no charge|complimentary)\b[^.]{0,40}\b(service|repair|installation|replacement)\b"),
    ("legal_advice", r"\blegal advice\b"),
    ("legal_advice", r"\b(legally (binding|obligated|required|entitled))\b"),
    ("legal_advice", r"\byou (will|can|should) (sue|win (your|the) case|be liable)\b"),
    ("legal_advice", r"\bwe (accept|admit) (liability|fault)\b"),
    ("medical_advice", r"\bmedical advice\b"),
    ("medical_advice", r"\byou (have|are experiencing) (a|an) [\w\s]{0,30}(condition|disease|infection|disorder)\b"),
    ("medical_advice", r"\b(diagnos(e|is|ed|ing)|prescri(be|bed|ption))\b"),
]


@pytest.fixture()
def seeded_guardrail_rules(db_session):
    """check_for_disallowed_commitments now reads GuardrailRule rows out of
    the DB instead of hardcoded Python constants - seed the same patterns
    the old constants held so this file's assertions keep testing real
    guardrail behavior."""
    for category, pattern in _SEED_PATTERNS:
        db_session.add(GuardrailRule(category=category, pattern=pattern))
    db_session.commit()
    return db_session


def test_clean_text_has_no_flags(seeded_guardrail_rules):
    assert check_for_disallowed_commitments(seeded_guardrail_rules, "Jordan called about a delayed shipment.") == []


def test_none_and_empty_texts_are_ignored(seeded_guardrail_rules):
    assert check_for_disallowed_commitments(seeded_guardrail_rules, None, "", None) == []
    assert check_for_disallowed_commitments(seeded_guardrail_rules) == []


def test_flags_a_dollar_amount_commitment(seeded_guardrail_rules):
    flags = check_for_disallowed_commitments(seeded_guardrail_rules, "We can fix that for $50 today.")
    assert "pricing_commitment" in flags


def test_flags_a_guaranteed_price(seeded_guardrail_rules):
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules, "I guaranteed the customer a discount on their next order."
    )
    assert "pricing_commitment" in flags


def test_flags_legal_advice_language(seeded_guardrail_rules):
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules, "Told the caller they are legally entitled to a full refund."
    )
    assert "legal_advice" in flags


def test_flags_medical_diagnosis_language(seeded_guardrail_rules):
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules, "Diagnosed the caller's symptoms as a sinus infection."
    )
    assert "medical_advice" in flags


def test_flags_multiple_categories_at_once(seeded_guardrail_rules):
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules, "Quoted a price of $200 and told them they are legally entitled to a refund."
    )
    assert "pricing_commitment" in flags
    assert "legal_advice" in flags


def test_does_not_flag_the_word_medical_or_legal_used_generically(seeded_guardrail_rules):
    """A caller mentioning e.g. a medical clinic or a legal firm by name is
    completely normal - only the specific disallowed-commitment phrasing
    should trip a flag, not any incidental use of a related word."""
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules, "Caller works at Downtown Medical Clinic and needs a callback."
    )
    assert flags == []


def test_checks_multiple_text_arguments_together(seeded_guardrail_rules):
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules, "Called about billing.", "Promised a $30 refund on the account."
    )
    assert "pricing_commitment" in flags


def test_scans_every_llm_extracted_field_not_just_summary_and_reason(seeded_guardrail_rules):
    """Guardrail previously only scanned summary/reason - spam_reason,
    callback_preference, caller_name, and caller_company come from the same
    untrusted LLM call and must be scanned too."""
    flags = check_for_disallowed_commitments(
        seeded_guardrail_rules,
        "Called about billing.", None, None, "Call back at this number, I guarantee a refund of the price.", None, None,
    )
    assert "pricing_commitment" in flags


def test_inactive_rule_is_not_applied(seeded_guardrail_rules):
    """is_active=False rules (e.g. a rule staff has retired) must not still
    flag content - this is the whole point of moving rules to a table
    instead of always-on hardcoded constants."""
    seeded_guardrail_rules.add(GuardrailRule(category="pricing_commitment", pattern=r"\bwidget special\b", is_active=False))
    seeded_guardrail_rules.commit()
    flags = check_for_disallowed_commitments(seeded_guardrail_rules, "Ask about our widget special.")
    assert flags == []
