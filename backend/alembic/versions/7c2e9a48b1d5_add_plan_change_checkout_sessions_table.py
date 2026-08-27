"""add plan change checkout sessions table

Revision ID: 7c2e9a48b1d5
Revises: 245b5ab62c15
Create Date: 2026-08-25 12:00:00.000000

ZL-COM-ENT-001-adjacent gap fix: PUT /subscription/plan previously applied a
plan upgrade's entitlements immediately with zero Stripe payment collection
(Production Readiness Standard doc: "A payment-success UI is not the same
as an authoritative paid invoice" / Global Pricing doc: Stripe live payments
is a P0 blocker). This table is the holding record between "customer asked
to upgrade" and "Stripe confirmed the charge" - see
app.billing.service.create_plan_change_checkout_session/
handle_stripe_checkout_completed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7c2e9a48b1d5'
down_revision: Union[str, None] = '245b5ab62c15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    plan_change_checkout_session_status_enum = postgresql.ENUM(
        'PENDING', 'COMPLETED', name='plan_change_checkout_session_status_enum', create_type=False
    )
    plan_change_checkout_session_status_enum.create(op.get_bind(), checkfirst=True)

    # billing_period_enum already exists (created by PriceCatalogEntry's
    # migration) - reused here, not recreated.
    billing_period_enum = postgresql.ENUM(
        'MONTHLY', 'ANNUAL', name='billing_period_enum', create_type=False
    )

    op.create_table(
        'plan_change_checkout_sessions',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('plan_code', sa.String(length=50), nullable=False),
        sa.Column('billing_period', billing_period_enum, nullable=False),
        sa.Column('stripe_session_id', sa.String(length=255), nullable=False),
        sa.Column('status', plan_change_checkout_session_status_enum, nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('stripe_session_id', name='uq_plan_change_checkout_sessions_stripe_session_id'),
    )
    op.create_index(
        op.f('ix_plan_change_checkout_sessions_account_id'), 'plan_change_checkout_sessions', ['account_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_plan_change_checkout_sessions_stripe_session_id'), 'plan_change_checkout_sessions',
        ['stripe_session_id'], unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_plan_change_checkout_sessions_stripe_session_id'), table_name='plan_change_checkout_sessions'
    )
    op.drop_index(op.f('ix_plan_change_checkout_sessions_account_id'), table_name='plan_change_checkout_sessions')
    op.drop_table('plan_change_checkout_sessions')

    postgresql.ENUM(
        name='plan_change_checkout_session_status_enum', create_type=False
    ).drop(op.get_bind(), checkfirst=True)
