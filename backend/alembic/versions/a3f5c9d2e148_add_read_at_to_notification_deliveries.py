"""add read_at to notification deliveries

Revision ID: a3f5c9d2e148
Revises: e1359a68dde8
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f5c9d2e148'
down_revision: Union[str, None] = 'e1359a68dde8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notification_deliveries", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("notification_deliveries", "read_at")
