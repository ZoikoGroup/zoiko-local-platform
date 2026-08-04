"""add guardrail flags to receptionist calls

Revision ID: e1359a68dde8
Revises: 445b6c830f92
Create Date: 2026-08-04 11:53:43.912525

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1359a68dde8'
down_revision: Union[str, None] = '445b6c830f92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "receptionist_calls",
        sa.Column("guardrail_flags", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("receptionist_calls", "guardrail_flags", server_default=None)


def downgrade() -> None:
    op.drop_column("receptionist_calls", "guardrail_flags")
