"""add price catalog entries table

Revision ID: e0f8f78c88dd
Revises: d3f5b0c9a247
Create Date: 2026-08-12 13:50:39.043046

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e0f8f78c88dd'
down_revision: Union[str, None] = 'd3f5b0c9a247'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Note: autogenerate also picked up pre-existing, unrelated drift (an
# 'agent_presence' unique-constraint name and 'calling_rates'
# nullability/constraint-naming mismatch) - deliberately left out, same
# as ce2bebedfe43's and prior migrations' notes.


def upgrade() -> None:
    op.create_table('price_catalog_entries',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('plan_code', sa.String(length=50), nullable=False),
    sa.Column('catalog_version', sa.String(length=50), nullable=False),
    sa.Column('amount_minor_units', sa.Integer(), nullable=False),
    sa.Column('currency_code', sa.String(length=3), nullable=False),
    sa.Column('status', sa.Enum('DRAFT', 'APPROVED', name='catalog_entry_status_enum'), nullable=False),
    sa.Column('is_placeholder', sa.Boolean(), nullable=False),
    sa.Column('approved_by', sa.String(length=100), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['plan_code'], ['plans.plan_code'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('plan_code', 'catalog_version', name='uq_price_catalog_entry_plan_version')
    )
    op.create_index(op.f('ix_price_catalog_entries_plan_code'), 'price_catalog_entries', ['plan_code'], unique=False)

    # Seed the same placeholder prices that used to live in the bare
    # TEST_PLACEHOLDER_PRICES Python dict (app.integrations.billing.
    # zoikonex) - same numbers, now a real versioned catalog row per plan
    # instead of a dict literal. is_placeholder=True, status left at its
    # DRAFT default - see PriceCatalogEntry's docstring for why these can
    # never just become APPROVED as-is.
    entries_table = sa.table(
        'price_catalog_entries',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('plan_code', sa.String),
        sa.column('catalog_version', sa.String),
        sa.column('amount_minor_units', sa.Integer),
        sa.column('currency_code', sa.String),
        sa.column('status', sa.String),
        sa.column('is_placeholder', sa.Boolean),
    )
    op.bulk_insert(
        entries_table,
        [
            {
                'id': str(uuid.uuid4()), 'plan_code': plan_code,
                'catalog_version': 'v1-placeholder', 'amount_minor_units': amount,
                'currency_code': 'USD', 'status': 'DRAFT', 'is_placeholder': True,
            }
            for plan_code, amount in [
                ('starter', 1999), ('business', 4999), ('enterprise', 19999),
            ]
        ],
    )

    # Same segregation-of-duties bar as billing.run_billing_cycle - locking/
    # changing a price is a real commercial decision.
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(grants_table, [{'id': str(uuid.uuid4()), 'capability': 'billing.manage_price_catalog', 'role': 'SUPER_ADMIN'}])


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'billing.manage_price_catalog'")
    op.drop_index(op.f('ix_price_catalog_entries_plan_code'), table_name='price_catalog_entries')
    op.drop_table('price_catalog_entries')
    op.execute("DROP TYPE IF EXISTS catalog_entry_status_enum")
