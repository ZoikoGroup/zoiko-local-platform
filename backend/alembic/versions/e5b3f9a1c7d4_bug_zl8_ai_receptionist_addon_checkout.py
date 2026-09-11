"""Bug ZL-8: real Stripe payment gate for the AI Receptionist add-on

Revision ID: e5b3f9a1c7d4
Revises: d4a7f2c6b8e1
Create Date: 2026-09-11 12:00:00.000000

Tester-reported, confirmed live: PUT /billing/subscription/ai-receptionist-
addon activated a real $29/workspace/month paid add-on with zero payment
collection - the same "payment-success UI is not the same as an
authoritative paid invoice" gap create_plan_change_checkout_session already
closed for plan upgrades, just never applied here. Adds the same
checkout-session holding table + a dedicated Stripe subscription id column
on `subscriptions` (kept separate from the existing stripe_subscription_id,
which is the PLAN's own Stripe subscription).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM as PGEnum
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = 'e5b3f9a1c7d4'
down_revision: Union[str, None] = 'd4a7f2c6b8e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('ai_receptionist_addon_stripe_subscription_id', sa.String(255), nullable=True))

    op.create_table(
        'ai_receptionist_addon_checkout_sessions',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column('account_id', UUID(as_uuid=False), sa.ForeignKey('accounts.id'), nullable=False, index=True),
        sa.Column('stripe_session_id', sa.String(255), nullable=False, unique=True, index=True),
        sa.Column(
            'status',
            PGEnum('PENDING', 'COMPLETED', name='plan_change_checkout_session_status_enum', create_type=False),
            nullable=False, server_default='PENDING',
        ),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('ai_receptionist_addon_checkout_sessions')
    op.drop_column('subscriptions', 'ai_receptionist_addon_stripe_subscription_id')
