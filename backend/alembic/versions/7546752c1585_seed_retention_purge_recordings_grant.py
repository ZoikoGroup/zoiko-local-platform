"""seed retention.purge_recordings grant

Revision ID: 7546752c1585
Revises: 8b345518d881
Create Date: 2026-08-27 18:00:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '7546752c1585'
down_revision: Union[str, None] = '8b345518d881'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backs POST /retention/purge, which was gated only by get_current_staff
    # (any staff role, including read-only SUPPORT) despite being the most
    # destructive action in the module - it deletes real recordings via
    # Twilio/S3 across every account. SUPER_ADMIN only, same bar as the
    # sibling retention.resolve_erasure_requests capability. Without this
    # row, require_capability fails closed and the route 403s for every
    # role including SUPER_ADMIN (see app.core.deps.require_capability's
    # docstring).
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "retention.purge_recordings", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_capability_grants WHERE capability = 'retention.purge_recordings'"
    )
