"""seed risk.manage_fraud_rules grant

Revision ID: d05ac876b3ab
Revises: c8b5d06cad1a
Create Date: 2026-08-10 17:05:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd05ac876b3ab'
down_revision: Union[str, None] = 'c8b5d06cad1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # app/risk/routes.py's PUT /risk/fraud-rules/{signal_type} gates on this
    # capability (introduced merging anilupdated's staff-tunable fraud-rule
    # weights onto this branch's RBAC capability-matrix system) but no
    # migration ever seeded a grant for it - without this row the endpoint
    # 403s for every staff role, same bar as risk.resolve_fraud_case
    # (ce2bebedfe43) but SUPER_ADMIN-only since tuning the scoring model
    # itself is more sensitive than resolving one case.
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "risk.manage_fraud_rules", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'risk.manage_fraud_rules'"
    )
