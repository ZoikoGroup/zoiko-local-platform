from app.intelligence.guardrails import check_for_disallowed_commitments


def test_clean_text_has_no_flags():
    assert check_for_disallowed_commitments("Jordan called about a delayed shipment.") == []


def test_none_and_empty_texts_are_ignored():
    assert check_for_disallowed_commitments(None, "", None) == []
    assert check_for_disallowed_commitments() == []


def test_flags_a_dollar_amount_commitment():
    flags = check_for_disallowed_commitments("We can fix that for $50 today.")
    assert "pricing_commitment" in flags


def test_flags_a_guaranteed_price():
    flags = check_for_disallowed_commitments("I guaranteed the customer a discount on their next order.")
    assert "pricing_commitment" in flags


def test_flags_legal_advice_language():
    flags = check_for_disallowed_commitments("Told the caller they are legally entitled to a full refund.")
    assert "legal_advice" in flags


def test_flags_medical_diagnosis_language():
    flags = check_for_disallowed_commitments("Diagnosed the caller's symptoms as a sinus infection.")
    assert "medical_advice" in flags


def test_flags_multiple_categories_at_once():
    flags = check_for_disallowed_commitments(
        "Quoted a price of $200 and told them they are legally entitled to a refund."
    )
    assert "pricing_commitment" in flags
    assert "legal_advice" in flags


def test_does_not_flag_the_word_medical_or_legal_used_generically():
    """A caller mentioning e.g. a medical clinic or a legal firm by name is
    completely normal - only the specific disallowed-commitment phrasing
    should trip a flag, not any incidental use of a related word."""
    flags = check_for_disallowed_commitments("Caller works at Downtown Medical Clinic and needs a callback.")
    assert flags == []


def test_checks_multiple_text_arguments_together():
    flags = check_for_disallowed_commitments("Called about billing.", "Promised a $30 refund on the account.")
    assert "pricing_commitment" in flags
