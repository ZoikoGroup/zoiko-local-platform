"""add risk_signals table

Revision ID: a80b7b11ce8e
Revises: 9158690e2d3a
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a80b7b11ce8e'
down_revision: Union[str, None] = '9158690e2d3a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('risk_signals',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('signal_type', sa.Enum('velocity_exceeded', 'blocked_destination_attempt', name='risksignaltype'), nullable=False),
    sa.Column('detail', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_risk_signals_account_id'), 'risk_signals', ['account_id'], unique=False)
    op.create_index(op.f('ix_risk_signals_created_at'), 'risk_signals', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_risk_signals_created_at'), table_name='risk_signals')
    op.drop_index(op.f('ix_risk_signals_account_id'), table_name='risk_signals')
    op.drop_table('risk_signals')
    sa.Enum(name='risksignaltype').drop(op.get_bind(), checkfirst=True)
