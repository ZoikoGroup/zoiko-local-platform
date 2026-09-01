"""add created_at to crm_connections

Revision ID: 9acc9a1a81de
Revises: 2faee6bbefe0
Create Date: 2026-09-01 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9acc9a1a81de'
down_revision: Union[str, None] = '2faee6bbefe0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # crm_connections previously only had connected_at (semantically "when
    # this specific provider connection was linked", overwritten in spirit
    # on every reconnect) - never a true immutable row-creation timestamp,
    # violating the project's "every table needs a UUID primary key +
    # created_at" rule. Nullable=True initially so the backfill below can
    # populate existing rows before the NOT NULL is applied.
    op.add_column(
        'crm_connections',
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
    )
    # Backfill existing rows from connected_at - the closest existing proxy
    # for when the row was actually created.
    op.execute("UPDATE crm_connections SET created_at = connected_at WHERE created_at IS NULL")
    op.alter_column('crm_connections', 'created_at', nullable=False)
    op.create_index(op.f('ix_crm_connections_created_at'), 'crm_connections', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_crm_connections_created_at'), table_name='crm_connections')
    op.drop_column('crm_connections', 'created_at')
