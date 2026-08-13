"""seed risk.manage_account_risk_state grant

Revision ID: c4d8e1a5b9f0
Revises: b2e6c4a19f03
Create Date: 2026-08-13 18:05:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4d8e1a5b9f0'
down_revision: Union[str, None] = 'b2e6c4a19f03'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # app/risk/routes.py's PUT /risk/accounts/{account_id}/risk-state gates
    # on this capability - without a seeded grant row, require_capability
    # fails closed and 403s for every staff role, including SUPER_ADMIN
    # (same bar as risk.manage_fraud_rules's d05ac876b3ab). Granted to the
    # same two roles as risk.reinstate_account/risk.resolve_fraud_case
    # (ce2bebedfe43) since a manual risk-state override is exactly the same
    # sensitivity tier as reversing an auto-suspension or resolving a case.
    for role in ("SUPER_ADMIN", "COMPLIANCE_OFFICER"):
        op.execute(
            sa.text(
                """
                INSERT INTO staff_capability_grants (id, capability, role)
                SELECT :id, :capability, :role
                WHERE NOT EXISTS (
                    SELECT 1 FROM staff_capability_grants
                    WHERE capability = :capability AND role = :role
                )
                """
            ).bindparams(id=str(uuid.uuid4()), capability="risk.manage_account_risk_state", role=role)
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'risk.manage_account_risk_state'"
    )
