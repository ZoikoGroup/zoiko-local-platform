"""add plan_entitlements table and seed entitlement keys

Revision ID: 710c98f42bfc
Revises: 71707ef5dc0a
Create Date: 2026-08-24 13:27:34.311401

ZL-COM-ENT-001 "Commercial Entitlement Governance" standard §5-6: explicit
entitlement keys, never ordinal plan-rank comparisons. Before this table,
these keys' features were completely ungated by plan - confirmed live:
any Starter/Business account could create API keys, publish call flows,
and pull analytics for free (see app.billing.service.has_entitlement, and
the routes that now call app.core.deps.require_entitlement /
require_entitlement_for_api_key). Seeded only for starter/business/pro/
scale - free_trial and enterprise get no rows, which correctly denies
every key by default (has_entitlement's deny-by-default fallback) rather
than needing special-casing here; a future Enterprise-contract pass will
seed those explicitly per-contract. `ai_receptionist.enabled` is seeded
here too but unused by application code - AI Receptionist's real gate is
billing_service.is_ai_receptionist_enabled_for_account, which reads
Plan.included_ai_receptionist_minutes/Subscription.ai_receptionist_addon_
enabled directly rather than this table; left seeded rather than editing
an already-applied migration's data for a harmless unused key.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '710c98f42bfc'
down_revision: Union[str, None] = '71707ef5dc0a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_KEYS = ['developer.api', 'developer.webhooks', 'routing.advanced', 'reporting.advanced', 'ai_receptionist.enabled']
_GRANTED_PLANS = {'pro', 'scale'}
_ALL_PLANS = ['starter', 'business', 'pro', 'scale']


def upgrade() -> None:
    op.create_table(
        'plan_entitlements',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('plan_code', sa.String(length=50), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value_type', sa.Enum('BOOLEAN', 'INTEGER', name='entitlement_value_type_enum'), nullable=False),
        sa.Column('bool_value', sa.Boolean(), nullable=True),
        sa.Column('int_value', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['plan_code'], ['plans.plan_code'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_code', 'key', name='uq_plan_entitlement_plan_key'),
    )
    op.create_index(op.f('ix_plan_entitlements_plan_code'), 'plan_entitlements', ['plan_code'], unique=False)
    op.create_index(op.f('ix_plan_entitlements_key'), 'plan_entitlements', ['key'], unique=False)

    entitlements_table = sa.table(
        'plan_entitlements',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('plan_code', sa.String),
        sa.column('key', sa.String),
        sa.column('value_type', sa.String),
        sa.column('bool_value', sa.Boolean),
    )
    op.bulk_insert(
        entitlements_table,
        [
            {
                'id': str(uuid.uuid4()), 'plan_code': plan_code, 'key': key,
                'value_type': 'BOOLEAN', 'bool_value': plan_code in _GRANTED_PLANS,
            }
            for plan_code in _ALL_PLANS
            for key in _KEYS
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_plan_entitlements_key'), table_name='plan_entitlements')
    op.drop_index(op.f('ix_plan_entitlements_plan_code'), table_name='plan_entitlements')
    op.drop_table('plan_entitlements')
    sa.Enum(name='entitlement_value_type_enum').drop(op.get_bind(), checkfirst=True)
