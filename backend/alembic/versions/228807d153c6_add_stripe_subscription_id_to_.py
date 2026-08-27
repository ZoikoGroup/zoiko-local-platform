"""add stripe_subscription_id to subscriptions

Revision ID: 228807d153c6
Revises: 9f4b2a7c1e83
Create Date: 2026-08-26 11:24:05.824214

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '228807d153c6'
down_revision: Union[str, None] = '9f4b2a7c1e83'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "stripe_subscription_id")
