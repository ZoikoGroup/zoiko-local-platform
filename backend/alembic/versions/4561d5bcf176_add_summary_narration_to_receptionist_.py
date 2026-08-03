"""add summary narration to receptionist_calls

Revision ID: 4561d5bcf176
Revises: f177a715ba1a
Create Date: 2026-08-03 17:10:49.773074

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4561d5bcf176'
down_revision: Union[str, None] = 'f177a715ba1a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("receptionist_calls", sa.Column("summary", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("receptionist_calls", "summary")
