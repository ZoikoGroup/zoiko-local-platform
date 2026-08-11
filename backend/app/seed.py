"""Seed a demo account for local development.

Run with: python -m app.seed
"""

from app.compliance.models import ComplianceRule
from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
from app.numbering.identity import service
from app.staff import service as staff_service
from app.staff.models import PlatformStaffRole

# Tier A / Tier B markets per the Roadmap doc's Phase 1 Launch Market Doctrine.
COMPLIANCE_RULES = [
    # Tier A - anchor markets
    {"country": "US", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "GB", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "CA", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    # Tier B - high-value growth corridors
    {"country": "NG", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address", "bvn"]},
    {"country": "ZA", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "GH", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "KE", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address"]},
    {"country": "MX", "requirement_type": "kyc_individual", "required_documents": ["government_id", "proof_of_address", "curp"]},
]


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
    db.commit()
    print(f"Seeded {len(COMPLIANCE_RULES)} compliance rules (skipping any that already exist)")


def run():
    # This seeds demo/staff accounts with hardcoded, checked-into-this-repo
    # passwords (see below) - fine for a throwaway local dev database, a
    # standing backdoor superadmin if ever run against a real one. Refuses
    # to run anywhere DATABASE_URL might plausibly be a real deployment.
    if settings.environment != "development":
        raise RuntimeError(
            f"Refusing to run app.seed with environment={settings.environment!r} - "
            "this creates hardcoded demo credentials (including a SUPER_ADMIN staff "
            "account) and must only ever run against a local development database."
        )

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
