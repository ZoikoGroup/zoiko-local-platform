"""add provisioning_started_at to phone_numbers

Revision ID: 1976d339ec28
Revises: f97acd504f57
Create Date: 2026-08-04 15:38:26.300063

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1976d339ec28'
down_revision: Union[str, None] = 'f97acd504f57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("phone_numbers", sa.Column("provisioning_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("phone_numbers", "provisioning_started_at")
