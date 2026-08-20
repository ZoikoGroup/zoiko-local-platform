"""add duration_seconds to receptionist_calls (Pricing doc §5.3 AI-minute metering)

Revision ID: 9b4e2f7a1c63
Revises: 697a995390f1
Create Date: 2026-08-20 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9b4e2f7a1c63'
down_revision: Union[str, None] = '697a995390f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('receptionist_calls', sa.Column('duration_seconds', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('receptionist_calls', 'duration_seconds')
