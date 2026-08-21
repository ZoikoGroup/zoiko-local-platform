"""add billing.terminate_subscription capability grant

Revision ID: 2c8b9960fe70
Revises: 2cb8cec0ce38
Create Date: 2026-08-19 00:00:01.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2c8b9960fe70'
down_revision: Union[str, None] = '2cb8cec0ce38'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same segregation-of-duties bar as billing.run_billing_cycle
# (a1c3d8f2e5b7) - terminating a subscription deprovisions real numbers and
# is a one-way door, at least as sensitive as staging a billing cycle.
_GRANTS: list[tuple[str, list[str]]] = [
    ("billing.terminate_subscription", ["SUPER_ADMIN"]),
]


def upgrade() -> None:
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [
            {"id": str(uuid.uuid4()), "capability": capability, "role": role}
            for capability, roles in _GRANTS
            for role in roles
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'billing.terminate_subscription'")
