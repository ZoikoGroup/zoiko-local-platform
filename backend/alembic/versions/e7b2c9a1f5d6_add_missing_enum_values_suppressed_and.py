"""add missing enum values (SUPPRESSED, VIEWER) never actually applied by earlier migrations

Revision ID: e7b2c9a1f5d6
Revises: d4a1f6c2b8e3
Create Date: 2026-08-10 16:35:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e7b2c9a1f5d6'
down_revision: Union[str, None] = 'd4a1f6c2b8e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Found via a full enum-value audit (alembic autogenerate never diffs
    # enum VALUES, only table/column structure, so these were invisible to
    # every earlier schema-drift check this session) - same "migration
    # stamped past without its DDL actually running" root cause as the
    # audit_events/plans/notification_templates gaps fixed earlier today.
    #
    # user_role_enum missing 'VIEWER' is a real production bug: any attempt
    # to create a viewer-role team member currently fails outright.
    op.execute("ALTER TYPE notification_delivery_status_enum ADD VALUE IF NOT EXISTS 'SUPPRESSED'")
    op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'VIEWER'")


def downgrade() -> None:
    # No DROP VALUE in Postgres - permanent no-op, same as every other
    # enum-extending migration in this codebase.
    pass
