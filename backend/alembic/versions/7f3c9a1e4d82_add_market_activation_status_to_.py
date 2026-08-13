"""add market activation status to supported_countries

Revision ID: 7f3c9a1e4d82
Revises: 18b3b905ae8c
Create Date: 2026-08-13 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7f3c9a1e4d82'
down_revision: Union[str, None] = '18b3b905ae8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Production Readiness Standard doc §6.2 "Market Activation Registry" -
# CLOSED/INTERNAL_TEST/CONTROLLED_BETA/PAID_OPEN/SUSPENDED, replacing the
# old binary "row exists in supported_countries or not" activation model.
#
# Backfills every EXISTING supported_countries row to PAID_OPEN - an honest
# "preserve current de facto behavior" carry-forward (these countries are
# already fully sellable in this dev/demo build today), not a real
# Legal/Tax/Compliance PAID_OPEN sign-off per that doc's §6.3 minimum
# market file. Any country added after this migration defaults to CLOSED
# (fail-closed, matching Annex B's "default-deny" principle) via the model
# column default - this backfill is a one-time exception for the countries
# that already existed before this control existed.


def upgrade() -> None:
    market_activation_status_enum = postgresql.ENUM(
        'CLOSED', 'INTERNAL_TEST', 'CONTROLLED_BETA', 'PAID_OPEN', 'SUSPENDED',
        name='market_activation_status_enum',
    )
    market_activation_status_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'supported_countries',
        sa.Column(
            'market_status',
            postgresql.ENUM(name='market_activation_status_enum', create_type=False),
            nullable=False,
            server_default='CLOSED',
        ),
    )
    op.execute("UPDATE supported_countries SET market_status = 'PAID_OPEN'")
    op.alter_column('supported_countries', 'market_status', server_default=None)


def downgrade() -> None:
    op.drop_column('supported_countries', 'market_status')
    sa.Enum(name='market_activation_status_enum').drop(op.get_bind(), checkfirst=True)
