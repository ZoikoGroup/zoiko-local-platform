"""add viewer role

Revision ID: e660c33b001a
Revises: 141799fdba9b
Create Date: 2026-08-04 19:39:17.425689

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e660c33b001a'
down_revision: Union[str, None] = '141799fdba9b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE user_role_enum ADD VALUE IF NOT EXISTS 'VIEWER'")


def downgrade() -> None:
    # Postgres can't drop a single enum value without recreating the whole
    # type - not worth it for a downgrade path (same rationale as every
    # other enum-add migration in this chain).
    pass
