"""Curated launch country list (Architecture doc's "6-8 curated countries",
not "whatever Twilio happens to expose"). Twilio itself has numbering
coverage well beyond this list, but search/reserve/purchase are
deliberately restricted to it - a customer picking from an unreviewed
100+ country list would routinely hit countries with numbering rules,
tax, or compliance requirements this platform hasn't reviewed yet."""

SUPPORTED_COUNTRIES: list[dict] = [
    {"code": "US", "name": "United States"},
    {"code": "CA", "name": "Canada"},
    {"code": "GB", "name": "United Kingdom"},
    {"code": "AU", "name": "Australia"},
    {"code": "DE", "name": "Germany"},
    {"code": "FR", "name": "France"},
    {"code": "IN", "name": "India"},
    {"code": "SG", "name": "Singapore"},
]

SUPPORTED_COUNTRY_CODES: frozenset[str] = frozenset(c["code"] for c in SUPPORTED_COUNTRIES)
