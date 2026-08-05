"""add editable AI output fields to summaries and receptionist calls

Revision ID: f97acd504f57
Revises: e1359a68dde8
Create Date: 2026-08-04 14:06:57.217300

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f97acd504f57'
down_revision: Union[str, None] = 'e1359a68dde8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for table in ("conversation_summaries", "receptionist_calls"):
        op.add_column(table, sa.Column("original_summary", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "edited_by_user_id", postgresql.UUID(as_uuid=False),
                sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
            ),
        )


def downgrade() -> None:
    for table in ("conversation_summaries", "receptionist_calls"):
        op.drop_column(table, "edited_by_user_id")
        op.drop_column(table, "edited_at")
        op.drop_column(table, "original_summary")
