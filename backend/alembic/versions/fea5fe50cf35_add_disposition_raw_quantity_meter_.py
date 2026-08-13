"""add disposition, raw_quantity, meter_version to usage_events

Revision ID: fea5fe50cf35
Revises: 013aad3fd9fb
Create Date: 2026-08-12 13:19:40.407347

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fea5fe50cf35'
down_revision: Union[str, None] = '013aad3fd9fb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §E1/§E5/§E6/§30 - disposition
    # + raw_quantity make usage evidence traceable back to what the provider
    # actually reported, independent of the (possibly floored/zeroed)
    # billed `quantity`; meter_version pins which rating rule produced that
    # figure. See app.usage.models.UsageEvent's docstrings.
    op.add_column('usage_events', sa.Column('disposition', sa.String(length=30), nullable=True))
    op.add_column('usage_events', sa.Column('raw_quantity', sa.Numeric(precision=12, scale=2), nullable=True))
    op.add_column(
        'usage_events',
        sa.Column('meter_version', sa.String(length=20), nullable=False, server_default='v1'),
    )


def downgrade() -> None:
    op.drop_column('usage_events', 'meter_version')
    op.drop_column('usage_events', 'raw_quantity')
    op.drop_column('usage_events', 'disposition')
