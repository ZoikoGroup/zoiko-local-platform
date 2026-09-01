"""seed staff.manage_staff grant

Revision ID: b3e7a1d9c624
Revises: 9f1c6d4a2b83
Create Date: 2026-08-31 00:00:00.000000

Real gap fix: POST/PUT /staff/members (adding/deactivating a staff
account) gates on staff.manage_staff, but no migration ever seeded a
grant for it - without this row the endpoints 403 for every staff role,
including SUPER_ADMIN, same bar as staff.manage_capabilities
(8e3f1a5d92c7) since granting someone staff console access at all is at
least as sensitive as editing the capability matrix itself.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b3e7a1d9c624'
down_revision: Union[str, None] = '9f1c6d4a2b83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
        ).bindparams(id=str(uuid.uuid4()), capability="staff.manage_staff", role="SUPER_ADMIN")
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'staff.manage_staff'"
    )
