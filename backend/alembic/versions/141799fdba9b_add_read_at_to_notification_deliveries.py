"""add read_at to notification deliveries

Revision ID: 141799fdba9b
Revises: 323f90b0da5d
Create Date: 2026-08-04 17:37:11.270767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '141799fdba9b'
down_revision: Union[str, None] = '323f90b0da5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notification_deliveries", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("notification_deliveries", "read_at")
