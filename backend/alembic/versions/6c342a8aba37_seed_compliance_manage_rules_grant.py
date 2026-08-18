"""seed compliance.manage_rules grant

Revision ID: 6c342a8aba37
Revises: a7be96c38a85
Create Date: 2026-08-18 00:00:03.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '6c342a8aba37'
down_revision: Union[str, None] = 'a7be96c38a85'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # app/compliance/routes.py's PUT /compliance/staff/rules gates on this
    # capability - without a seeded grant row, require_capability fails
    # closed and 403s for every staff role, same bar as
    # risk.manage_fraud_rules (d05ac876b3ab). Granted to the same two roles
    # as compliance.review_case (ce2bebedfe43), since managing which
    # countries/requirement types actually gate number purchase is the same
    # sensitivity tier as reviewing an individual case.
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
            ).bindparams(id=str(uuid.uuid4()), capability="compliance.manage_rules", role=role)
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'compliance.manage_rules'"
    )
