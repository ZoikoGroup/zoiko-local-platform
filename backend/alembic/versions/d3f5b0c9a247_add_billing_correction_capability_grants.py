"""add billing correction/refund capability grants

Revision ID: d3f5b0c9a247
Revises: c7e2a4f68d91
Create Date: 2026-08-12 05:30:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f5b0c9a247'
down_revision: Union[str, None] = 'c7e2a4f68d91'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same segregation-of-duties bar as billing.run_billing_cycle (a1c3d8f2e5b7)
# - credit/debit notes and refunds are real, money-adjacent ZoikoNex writes.
_GRANTS: list[tuple[str, list[str]]] = [
    ("billing.issue_credit_note", ["SUPER_ADMIN"]),
    ("billing.issue_debit_note", ["SUPER_ADMIN"]),
    ("billing.refund_payment", ["SUPER_ADMIN"]),
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
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability IN "
        "('billing.issue_credit_note', 'billing.issue_debit_note', 'billing.refund_payment')"
    )
