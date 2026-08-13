"""add canceled_at to subscriptions

Revision ID: e08eeaf76017
Revises: 3fe8ba8f336f
Create Date: 2026-08-13 18:50:48.972700

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e08eeaf76017'
down_revision: Union[str, None] = '3fe8ba8f336f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dropped autogenerate's unrelated drift noise on agent_presence/calling_rates
# (pre-existing model/DB skew, not touched here - same drift stripped out of
# every migration this session).


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('subscriptions', 'canceled_at')
