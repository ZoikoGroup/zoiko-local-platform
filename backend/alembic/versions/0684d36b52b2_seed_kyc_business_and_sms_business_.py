"""seed kyc_business and sms_business_messaging compliance rules

Revision ID: 0684d36b52b2
Revises: f4a8c1d90b3e
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union
import json
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0684d36b52b2'
down_revision: Union[str, None] = 'f4a8c1d90b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same 8-country launch list as supported_countries (migration 155a3edc4305)
# and app/seed.py's dev-only COMPLIANCE_RULES. That script's run() refuses
# to execute against anything but environment=="development" (it also
# creates a hardcoded demo superadmin), so it has never populated a real
# deployed database - compliance_rules has been empty in every real
# environment until this migration, and two requirement types were missing
# entirely, not just unseeded in prod:
#   - kyc_business: gates number purchase for business accounts
#     (app/numbering/numbers/service.py classifies by AccountType) but had
#     zero rows for ANY country in ANY environment, dev included - a
#     business account could never be gated on KYC at all.
#   - sms_business_messaging: gates enabling SMS on a number
#     (SMS_REQUIREMENT_TYPE, same file) - also zero rows anywhere, making
#     that gate a permanent no-op regardless of environment, since
#     is_requirement_active() only returns True when a matching row exists.
# kyc_individual is re-asserted here too (idempotently) so a fresh
# production database ends up with the same baseline a freshly-seeded dev
# database already has, without depending on the dev-only script ever
# running against it.
_COUNTRIES = ["US", "GB", "CA", "AU", "DE", "FR", "IN", "SG"]

_RULES = [
    ("kyc_individual", ["government_id", "proof_of_address"]),
    ("kyc_business", ["business_registration", "proof_of_address"]),
    ("sms_business_messaging", ["business_registration"]),
]


def upgrade() -> None:
    for country in _COUNTRIES:
        for requirement_type, documents in _RULES:
            op.execute(
                sa.text(
                    """
                    INSERT INTO compliance_rules (id, country, requirement_type, required_documents, is_active)
                    SELECT :id, :country, :requirement_type, CAST(:documents AS JSON), TRUE
                    WHERE NOT EXISTS (
                        SELECT 1 FROM compliance_rules
                        WHERE country = :country AND requirement_type = :requirement_type
                    )
                    """
                ).bindparams(
                    id=str(uuid.uuid4()),
                    country=country,
                    requirement_type=requirement_type,
                    documents=json.dumps(documents),
                )
            )


def downgrade() -> None:
    op.execute(
        "DELETE FROM compliance_rules WHERE requirement_type IN "
        "('kyc_individual', 'kyc_business', 'sms_business_messaging')"
    )
