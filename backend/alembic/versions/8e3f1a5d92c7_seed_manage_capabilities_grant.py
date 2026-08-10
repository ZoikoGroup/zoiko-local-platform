"""seed staff.manage_capabilities grant

Revision ID: 8e3f1a5d92c7
Revises: 4d8b6f0c1a93
Create Date: 2026-08-10 16:20:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8e3f1a5d92c7'
down_revision: Union[str, None] = '4d8b6f0c1a93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Makes the RBAC capability matrix itself editable (grant/revoke) via
    # PUT/DELETE /staff/access-matrix/{capability}/{role}, gated by this
    # new capability - SUPER_ADMIN only, same bar as every other sensitive
    # staff write action. See app.staff.service.MATRIX_MANAGEMENT_CAPABILITY's
    # docstring for why this one is protected from ever reaching zero grants.
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "staff.manage_capabilities", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'staff.manage_capabilities'"
    )
