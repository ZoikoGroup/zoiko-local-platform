"""add provisioning error code and attempt count to phone numbers

Revision ID: c49b46c6bd85
Revises: c3b0f40f4bc1
Create Date: 2026-08-22 18:46:47.632712

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c49b46c6bd85'
down_revision: Union[str, None] = 'c3b0f40f4bc1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("phone_numbers", sa.Column("last_provisioning_error_code", sa.String(length=100), nullable=True))
    op.add_column(
        "phone_numbers", sa.Column("provisioning_attempt_count", sa.Integer(), nullable=False, server_default="0")
    )


def downgrade() -> None:
    op.drop_column("phone_numbers", "provisioning_attempt_count")
    op.drop_column("phone_numbers", "last_provisioning_error_code")
