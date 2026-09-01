"""seed staff.manage_staff_accounts grant

Revision ID: a4f7c2e8b19d
Revises: 228807d153c6
Create Date: 2026-08-27 00:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a4f7c2e8b19d'
down_revision: Union[str, None] = '228807d153c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backs POST/PUT /staff/team/* (create/deactivate/reactivate a staff
    # account) - SUPER_ADMIN only, same bar as staff.manage_capabilities.
    # Without this row, require_capability fails closed and the new
    # routes 403 for every role including SUPER_ADMIN (see
    # app.core.deps.require_capability's docstring).
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "staff.manage_staff_accounts", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'staff.manage_staff_accounts'"
    )
