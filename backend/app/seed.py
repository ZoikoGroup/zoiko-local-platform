"""Seed a demo account for local development.

Run with: python -m app.seed
"""

from app.compliance.models import ComplianceRule
from app.core.database import Base, SessionLocal, engine
from app.numbering.identity import service
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole

# Kept 1:1 with app.numbering.numbers.models.SupportedCountry's actual
# purchasable-country list - a rule for a country nobody can buy a number
# in is dead data that just confuses the next person reading this file (an
# earlier version of this list covered NG/ZA/GH/KE/MX, none of which are
# in SupportedCountry - removed, no ComplianceCase ever referenced them).
# "government_id" covers whichever accepted photo ID the customer submits
# (passport, Aadhaar, driving license, etc.) - document TYPE is captured
# per-upload (ComplianceCase.documents[].document_type), not enumerated per
# country here. Every market below uses the same generic two-category
# baseline; a market needing something more specific (like NG's old bvn
# addendum) should add it back deliberately, not by leaving stale rows
# from a country that isn't even sellable.
COMPLIANCE_RULES = [
    {"country": "US", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "GB", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "CA", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "AU", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "DE", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "FR", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "IN", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "SG", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
]

# Countries a ComplianceRule previously existed for but that were never in
# SupportedCountry (dead data - see COMPLIANCE_RULES's docstring above).
# Pruned by seed.py so a stale row left over from an old market-list
# decision doesn't linger in a DB that already ran an earlier seed.
_STALE_COMPLIANCE_RULE_COUNTRIES = ["NG", "ZA", "GH", "KE", "MX"]


def seed_compliance_rules(db):
    for rule in COMPLIANCE_RULES:
        existing = (
            db.query(ComplianceRule)
            .filter(
                ComplianceRule.country == rule["country"],
                ComplianceRule.requirement_type == rule["requirement_type"],
            )
            .first()
        )
        if existing:
            continue
        db.add(ComplianceRule(**rule))

    pruned = (
        db.query(ComplianceRule)
        .filter(ComplianceRule.country.in_(_STALE_COMPLIANCE_RULE_COUNTRIES))
        .delete(synchronize_session=False)
    )
    db.commit()
    print(f"Seeded {len(COMPLIANCE_RULES)} compliance rules (skipping any that already exist)")
    if pruned:
        print(f"Pruned {pruned} stale compliance rule(s) for countries not in SupportedCountry")


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        try:
            user = service.create_account_with_owner(
                db,
                account_name="Demo Account",
                account_type="individual",
                email="demo@zoikolocal.com",
                password="demo12345",
            )
            print(f"Seeded demo user: {user.email} (account_id={user.account_id})")
        except ValueError as e:
            print(f"Skipped seeding: {e}")

        seed_compliance_rules(db)

        try:
            staff = staff_service.create_staff(
                db, email="ops@zoikolocal.com", password="staffdemo12345", role=PlatformStaffRole.SUPER_ADMIN
            )
            print(f"Seeded demo staff account: {staff.email}")
        except ValueError as e:
            print(f"Skipped seeding staff: {e}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
