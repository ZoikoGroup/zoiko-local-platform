"""add reverification_due_at to compliance_cases

Revision ID: e5bf995f9481
Revises: 3aba8bcd72b6
Create Date: 2026-08-31 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5bf995f9481'
down_revision: Union[str, None] = '3aba8bcd72b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('compliance_cases', sa.Column('reverification_due_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('compliance_cases', 'reverification_due_at')
