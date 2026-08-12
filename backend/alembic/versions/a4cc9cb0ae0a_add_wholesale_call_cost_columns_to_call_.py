"""add wholesale call cost columns to call records

Revision ID: a4cc9cb0ae0a
Revises: df9eadb7ef87
Create Date: 2026-08-12 22:04:30.931905

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a4cc9cb0ae0a'
down_revision: Union[str, None] = 'df9eadb7ef87'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dropped autogenerate's unrelated drift noise on agent_presence/calling_rates
# (pre-existing model/DB skew, not touched here - same drift stripped out of
# every migration this session).


def upgrade() -> None:
    op.add_column('call_records', sa.Column('wholesale_cost_cents', sa.Integer(), nullable=True))
    op.add_column('call_records', sa.Column('wholesale_currency', sa.String(length=3), nullable=True))


def downgrade() -> None:
    op.drop_column('call_records', 'wholesale_currency')
    op.drop_column('call_records', 'wholesale_cost_cents')
