"""add usage event rated_at and late usage event reconciliation

Revision ID: df9eadb7ef87
Revises: 4e292a945c18
Create Date: 2026-08-12 20:10:19.398023

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'df9eadb7ef87'
down_revision: Union[str, None] = '4e292a945c18'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Dropped autogenerate's unrelated drift noise on agent_presence/calling_rates
# (pre-existing model/DB skew from an earlier migration, not touched here -
# same drift stripped out of every migration this session).
#
# This migration originally also added usage_events.meter_version, before
# discovering that origin/dev's fea5fe50cf35 (part of a concurrent session's
# "P0 items 8-15" commit) already adds a column of that exact name to that
# exact table, with different semantics (NOT NULL, rating-rule version,
# paired with disposition/raw_quantity) - a direct collision. That add_column
# call was removed here in favor of dev's version rather than fighting two
# meter_version columns through the merged migration graph; see the merge
# commit for the reconciliation.


def upgrade() -> None:
    op.add_column('usage_events', sa.Column('rated_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        'zoikonex_reconciliation_runs',
        sa.Column('late_usage_events', sa.Integer(), nullable=False, server_default='0'),
    )
    # Uppercase name, not the enum member's lowercase .value - SQLAlchemy's
    # Enum column serializes by member .name (see c7e2a4f68d91's docstring).
    op.execute(
        "ALTER TYPE zoikonex_reconciliation_exception_type_enum "
        "ADD VALUE IF NOT EXISTS 'LATE_USAGE_EVENT'"
    )


def downgrade() -> None:
    # No DROP VALUE in Postgres - permanent no-op for the enum value, same as
    # every other enum-extending migration in this codebase.
    op.drop_column('zoikonex_reconciliation_runs', 'late_usage_events')
    op.drop_column('usage_events', 'rated_at')
