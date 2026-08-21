"""grant super admin the event outbox flush capability

Revision ID: a651ca32ef6b
Revises: 10c3eaf78508
Create Date: 2026-08-19 12:28:26.443274

require_capability fails closed (app.core.deps.require_capability's own
docstring: "a capability with zero configured grants... denies every
role") - POST /ops/event-outbox/flush would 403 for every staff member,
including SUPER_ADMIN, without a real grant row. Same bar as the other
platform-wide ops triggers (ops.manage_kill_switches) - SUPER_ADMIN only.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a651ca32ef6b'
down_revision: Union[str, None] = '10c3eaf78508'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.String),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "ops.manage_event_outbox", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'ops.manage_event_outbox' AND role = 'SUPER_ADMIN'"
    )
