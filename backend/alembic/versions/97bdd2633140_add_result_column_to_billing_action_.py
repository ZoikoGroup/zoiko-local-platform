"""add result column to billing_action_requests

Revision ID: 97bdd2633140
Revises: 9ca2ebab2e4f
Create Date: 2026-08-12 15:27:35.534839

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '97bdd2633140'
down_revision: Union[str, None] = '9ca2ebab2e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The real ZoikoNex response once a BillingActionRequest is EXECUTED -
    # see BillingActionRequest.result's docstring.
    op.add_column('billing_action_requests', sa.Column('result', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('billing_action_requests', 'result')
