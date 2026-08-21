"""grant super admin the new number and ai usage rate capabilities

Revision ID: ba1a2c3c220b
Revises: 61bc6e50e6db
Create Date: 2026-08-17 12:10:33.077867

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ba1a2c3c220b'
down_revision: Union[str, None] = '61bc6e50e6db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# require_capability "fails closed" (see app.core.deps's docstring) - the
# staff PUT /staff/number-rates and /staff/ai-usage-rate routes added
# alongside 61bc6e50e6db's new rate tables need their own grant rows here,
# same SUPER_ADMIN-only bar as the pre-existing "billing.manage_calling_rates"
# (ce2bebedfe43_add_staff_capability_grants_matrix.py) - a pricing change is
# a platform-wide decision, not a routine support action.
_GRANTS: list[tuple[str, str]] = [
    ("billing.manage_number_rates", "SUPER_ADMIN"),
    ("billing.manage_ai_usage_rates", "SUPER_ADMIN"),
]


def upgrade() -> None:
    grants_table = sa.table(
        "staff_capability_grants",
        sa.column("id", sa.String),
        sa.column("capability", sa.String),
        sa.column("role", sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": capability, "role": role} for capability, role in _GRANTS],
    )


def downgrade() -> None:
    conn = op.get_bind()
    for capability, role in _GRANTS:
        conn.execute(
            sa.text("DELETE FROM staff_capability_grants WHERE capability = :capability AND role = :role"),
            {"capability": capability, "role": role},
        )
