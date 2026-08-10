"""add fraud rules and fraud cases for proprietary fraud model

Revision ID: 0d31d1ab1f2d
Revises: 6702184e75db
Create Date: 2026-08-10 10:57:50.686114

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0d31d1ab1f2d'
down_revision: Union[str, None] = '6702184e75db'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New RiskSignalType member - risksignaltype's existing labels
    # ('velocity_exceeded', 'blocked_destination_attempt') are lowercase
    # .value strings (not this codebase's usual uppercase .name convention -
    # see a80b7b11ce8e_add_risk_signals_table.py), so this follows the same
    # style already committed to Postgres for this specific enum. Safe
    # inside a transaction on PG12+ as long as the new label isn't used in
    # this same migration.
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'geographic_dispersion'")

    op.create_table(
        'fraud_rules',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            'signal_type',
            postgresql.ENUM(name='risksignaltype', create_type=False),
            nullable=False,
        ),
        sa.Column('weight', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('signal_type'),
    )

    op.create_table(
        'fraud_cases',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('score_at_open', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum('OPEN', 'CONFIRMED', 'CLEARED', name='fraudcasestatus'),
            nullable=False,
        ),
        sa.Column('resolved_by', sa.String(length=255), nullable=True),
        sa.Column('resolution_notes', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_fraud_cases_account_id'), 'fraud_cases', ['account_id'], unique=False)
    op.create_index(op.f('ix_fraud_cases_created_at'), 'fraud_cases', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_fraud_cases_created_at'), table_name='fraud_cases')
    op.drop_index(op.f('ix_fraud_cases_account_id'), table_name='fraud_cases')
    op.drop_table('fraud_cases')
    op.execute("DROP TYPE IF EXISTS fraudcasestatus")
    op.drop_table('fraud_rules')
    # Postgres has no ALTER TYPE ... DROP VALUE - a downgrade cannot cleanly
    # remove 'geographic_dispersion' from risksignaltype without rebuilding
    # the type (dropping/recreating it and every column that depends on
    # it). Left as a no-op, same tradeoff every other enum-extending
    # migration in this codebase makes.
