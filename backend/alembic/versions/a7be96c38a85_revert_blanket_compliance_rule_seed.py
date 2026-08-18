"""revert blanket compliance rule seed

Revision ID: a7be96c38a85
Revises: b52d3c2c6fc3
Create Date: 2026-08-18 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'a7be96c38a85'
down_revision: Union[str, None] = 'b52d3c2c6fc3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0684d36b52b2 force-seeded kyc_individual/kyc_business/sms_business_messaging
    # compliance rules for all 8 launch countries via an always-applied migration -
    # this ran identically in CI's test database (CI runs `alembic upgrade head`
    # before pytest, per .github/workflows/ci.yml), which broke ~40 pre-existing
    # tests across test_numbers.py/test_number_eligibility.py/test_sms_compliance.py/
    # test_risk.py/test_billing.py/test_fraud_model.py/test_e2e_acceptance.py/
    # test_zoikonex_mock.py that were written assuming compliance_rules starts
    # empty by default (each test that wants a rule inserts its own row, the
    # same "opt-in per test" shape as FraudRule/BlockedDestination). A bulk
    # migration seed is the wrong mechanism for this table - see
    # 6c342a8aba37 (the follow-up migration right after this one) for the
    # replacement: a staff-managed API, same posture as risk.manage_fraud_rules.
    op.execute(
        "DELETE FROM compliance_rules WHERE requirement_type IN "
        "('kyc_individual', 'kyc_business', 'sms_business_messaging')"
    )


def downgrade() -> None:
    # Deliberately a no-op - re-seeding on downgrade would just reintroduce
    # the same test breakage this migration exists to fix. Use the staff API
    # (compliance.manage_rules capability, see 6c342a8aba37) to add rules back.
    pass
