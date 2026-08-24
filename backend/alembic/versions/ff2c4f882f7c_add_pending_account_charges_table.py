"""add pending account charges table

Revision ID: ff2c4f882f7c
Revises: ab31fd0b79dc
Create Date: 2026-08-22 16:10:54.560621

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'ff2c4f882f7c'
down_revision: Union[str, None] = 'ab31fd0b79dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Uppercase labels match this codebase's convention of storing a Python
    # (str, Enum)'s *member name* in Postgres, since SQLAlchemy's Enum(...)
    # binds by .name (not .value) when given a Python enum class.
    pending_account_charge_status_enum = postgresql.ENUM(
        'PENDING', 'INVOICED', name='pending_account_charge_status_enum', create_type=False
    )
    pending_account_charge_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'pending_account_charges',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('charge_type', sa.String(length=30), nullable=False),
        sa.Column('phone_number_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('description', sa.String(length=255), nullable=False),
        sa.Column('amount_minor_units', sa.Integer(), nullable=False),
        sa.Column('currency_code', sa.String(length=3), nullable=False),
        sa.Column('status', pending_account_charge_status_enum, nullable=False),
        sa.Column('invoiced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('zoikonex_invoice_id', sa.String(length=100), nullable=True),
        sa.Column('zoikonex_line_item_id', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['phone_number_id'], ['phone_numbers.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_pending_account_charges_account_id'), 'pending_account_charges', ['account_id'], unique=False
    )
    op.create_index(
        op.f('ix_pending_account_charges_phone_number_id'), 'pending_account_charges', ['phone_number_id'], unique=False
    )
    op.create_index(
        op.f('ix_pending_account_charges_status'), 'pending_account_charges', ['status'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_pending_account_charges_status'), table_name='pending_account_charges')
    op.drop_index(op.f('ix_pending_account_charges_phone_number_id'), table_name='pending_account_charges')
    op.drop_index(op.f('ix_pending_account_charges_account_id'), table_name='pending_account_charges')
    op.drop_table('pending_account_charges')

    postgresql.ENUM(name='pending_account_charge_status_enum', create_type=False).drop(op.get_bind(), checkfirst=True)
