"""add billing.run_billing_cycle capability grant

Revision ID: a1c3d8f2e5b7
Revises: 7852c57ad1b9
Create Date: 2026-08-11 19:30:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1c3d8f2e5b7'
down_revision: Union[str, None] = '7852c57ad1b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same segregation-of-duties bar as billing.simulate_payment_event /
# billing.resolve_reconciliation_exception (ce2bebedfe43) - running a real
# ZoikoNex invoice + payment-intent cycle (even against TEST_PLACEHOLDER_PRICES)
# is money-adjacent state, not a read-only diagnostic.
_GRANTS: list[tuple[str, list[str]]] = [
    ("billing.run_billing_cycle", ["SUPER_ADMIN"]),
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
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'billing.run_billing_cycle'")
