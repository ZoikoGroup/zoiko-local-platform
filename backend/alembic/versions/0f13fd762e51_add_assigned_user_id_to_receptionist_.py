"""add assigned_user_id to receptionist calls

Revision ID: 0f13fd762e51
Revises: b1c499e25296
Create Date: 2026-08-04 10:20:13.845896

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0f13fd762e51'
down_revision: Union[str, None] = 'b1c499e25296'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "receptionist_calls",
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.create_foreign_key(
        "fk_receptionist_calls_assigned_user_id",
        "receptionist_calls", "users", ["assigned_user_id"], ["id"], ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_receptionist_calls_assigned_user_id", "receptionist_calls", type_="foreignkey")
    op.drop_column("receptionist_calls", "assigned_user_id")
