"""add scheduled plan-change columns to subscriptions

Revision ID: c2580a9fed09
Revises: fd300247929d
Create Date: 2026-08-27 00:10:00.000000

ZL-COM-ENT-001 v3.0 §8 - self-service downgrades take effect at the end of
the current paid period by default, not immediately. No scheduler exists
in this codebase - these columns are applied lazily by
billing.service.get_or_create_subscription's existing period-rollover
read path, not a background job.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c2580a9fed09'
down_revision: Union[str, None] = 'fd300247929d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('subscriptions', sa.Column('scheduled_plan_code', sa.String(length=50), nullable=True))
    op.create_foreign_key(
        'fk_subscriptions_scheduled_plan_code_plans', 'subscriptions', 'plans',
        ['scheduled_plan_code'], ['plan_code'],
    )
    # billing_period_enum already exists (created by PriceCatalogEntry's own
    # billing_period column) - create_type=False, same idiom 7c2e9a48b1d5
    # already uses for the same type.
    billing_period_enum = postgresql.ENUM('MONTHLY', 'ANNUAL', name='billing_period_enum', create_type=False)
    op.add_column('subscriptions', sa.Column('scheduled_billing_period', billing_period_enum, nullable=True))
    op.add_column('subscriptions', sa.Column('scheduled_change_effective_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('subscriptions', 'scheduled_change_effective_at')
    op.drop_column('subscriptions', 'scheduled_billing_period')
    op.drop_constraint('fk_subscriptions_scheduled_plan_code_plans', 'subscriptions', type_='foreignkey')
    op.drop_column('subscriptions', 'scheduled_plan_code')
