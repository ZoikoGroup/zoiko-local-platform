"""add ai receptionist trial cap risk signal type

Revision ID: e7c2b6f184a9
Revises: d1f7a3e9c052
Create Date: 2026-08-20 00:00:03.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e7c2b6f184a9'
down_revision: Union[str, None] = 'd1f7a3e9c052'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Type name is 'risksignaltype' (no underscore/suffix) - confirmed live
    # by 351fca0d8b24, not the usual '..._enum' convention this codebase
    # otherwise follows.
    op.execute("ALTER TYPE risksignaltype ADD VALUE 'AI_RECEPTIONIST_TRIAL_CAP_EXCEEDED'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE - same limitation noted by
    # every other enum-extending migration in this codebase.
    pass
