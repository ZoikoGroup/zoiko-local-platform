"""seed ops.manage_incidents grant

Revision ID: af272e35f51c
Revises: b8d4e2f6a1c9
Create Date: 2026-08-11 07:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'af272e35f51c'
down_revision: Union[str, None] = 'b8d4e2f6a1c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # app/ops/routes.py's incident create/update/resolve endpoints gate on
    # this capability (added when their require_staff_role(...) call sites
    # were migrated to the data-driven RBAC matrix), but no migration ever
    # seeded a grant for it - require_capability fails closed on a missing
    # grant, so without this row every staff role gets 403, including
    # SUPER_ADMIN. Same role set the routes originally hardcoded
    # (SUPPORT, SUPER_ADMIN) - declaring/resolving an incident is an
    # operational action, same bar as provisioning recovery and renewal
    # marking (numbers.manage_provisioning/manage_renewal).
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [
            {"id": str(uuid.uuid4()), "capability": "ops.manage_incidents", "role": "SUPPORT"},
            {"id": str(uuid.uuid4()), "capability": "ops.manage_incidents", "role": "SUPER_ADMIN"},
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'ops.manage_incidents'"
    )
