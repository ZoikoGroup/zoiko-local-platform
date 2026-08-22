"""add expires_at to platform and account kill switches

Revision ID: 9a6d3bb94cce
Revises: 76e1c10d6375
Create Date: 2026-08-22 16:47:14.276884

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a6d3bb94cce'
down_revision: Union[str, None] = '76e1c10d6375'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("platform_kill_switches", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("account_kill_switches", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("account_kill_switches", "expires_at")
    op.drop_column("platform_kill_switches", "expires_at")
