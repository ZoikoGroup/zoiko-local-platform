"""merge venky and anilupdated phase4 migration heads

Revision ID: e796f9fa547f
Revises: e7b2c9a1f5d6, 8e3f1a5d92c7
Create Date: 2026-08-11 11:16:12.598008

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e796f9fa547f'
down_revision: Union[str, None] = ('e7b2c9a1f5d6', '8e3f1a5d92c7')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Grants the new upsert_fraud_rule endpoint (anilupdated side) the same
    # data-driven RBAC gating venky's require_capability system already
    # applies to every other sensitive risk/fraud action in this merge -
    # SUPER_ADMIN only, same bar as risk.manage_blocked_destinations.
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
