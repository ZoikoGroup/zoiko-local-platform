"""usage_disputes + usage_adjustments: append-only billing correction trail

Revision ID: 9a3f7cd1b246
Revises: 47e1c4473435
Create Date: 2026-08-13 21:05:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9a3f7cd1b246'
down_revision: Union[str, None] = '47e1c4473435'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No explicit .create() here - op.create_table below creates a new
    # sa.Enum automatically; calling .create() first as well double-
    # creates it (see 47e1c4473435's fix-commit note for how this was
    # actually confirmed live, not just theorized).
    usage_dispute_status_enum = sa.Enum(
        'OPEN', 'INVESTIGATING', 'RESOLVED_ADJUSTED', 'RESOLVED_DENIED', name='usage_dispute_status_enum',
    )

    op.create_table(
        'usage_disputes',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('usage_event_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('status', usage_dispute_status_enum, nullable=False),
        sa.Column('raised_by', sa.String(length=100), nullable=False),
        sa.Column('resolved_by', sa.String(length=100), nullable=True),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['usage_event_id'], ['usage_events.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_usage_disputes_account_id'), 'usage_disputes', ['account_id'], unique=False)
    op.create_index(op.f('ix_usage_disputes_usage_event_id'), 'usage_disputes', ['usage_event_id'], unique=False)
    op.create_index(op.f('ix_usage_disputes_created_at'), 'usage_disputes', ['created_at'], unique=False)

    op.create_table(
        'usage_adjustments',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('usage_event_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('dispute_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('previous_estimated_cost_cents', sa.Integer(), nullable=True),
        sa.Column('new_estimated_cost_cents', sa.Integer(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('actor', sa.String(length=100), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['usage_event_id'], ['usage_events.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['dispute_id'], ['usage_disputes.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_usage_adjustments_usage_event_id'), 'usage_adjustments', ['usage_event_id'], unique=False)
    op.create_index(op.f('ix_usage_adjustments_created_at'), 'usage_adjustments', ['created_at'], unique=False)

    # Same segregation-of-duties bar as every other sensitive billing
    # action in this codebase (billing.manage_price_catalog, etc.).
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table, [{'id': str(uuid.uuid4()), 'capability': 'billing.resolve_usage_dispute', 'role': 'SUPER_ADMIN'}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'billing.resolve_usage_dispute'")
    op.drop_index(op.f('ix_usage_adjustments_created_at'), table_name='usage_adjustments')
    op.drop_index(op.f('ix_usage_adjustments_usage_event_id'), table_name='usage_adjustments')
    op.drop_table('usage_adjustments')
    op.drop_index(op.f('ix_usage_disputes_created_at'), table_name='usage_disputes')
    op.drop_index(op.f('ix_usage_disputes_usage_event_id'), table_name='usage_disputes')
    op.drop_index(op.f('ix_usage_disputes_account_id'), table_name='usage_disputes')
    op.drop_table('usage_disputes')
    op.execute("DROP TYPE IF EXISTS usage_dispute_status_enum")
