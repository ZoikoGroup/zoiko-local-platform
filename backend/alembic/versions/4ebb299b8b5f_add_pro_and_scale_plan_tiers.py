"""add pro and scale plan tiers

Revision ID: 4ebb299b8b5f
Revises: 9a3f7cd1b246
Create Date: 2026-08-14 09:35:56.855255

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4ebb299b8b5f'
down_revision: Union[str, None] = '9a3f7cd1b246'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Zoiko Local Global Plans, Pricing & Commercial Launch Standard (executive
# doc, 14 Aug 2026) approves a real 5-tier architecture - Starter/Business/
# Pro/Scale/Enterprise - but this codebase only had 4 plan_codes (no Pro or
# Scale). Entitlement NUMBERS below (max_numbers/seats/minutes) are NOT
# specified anywhere in that doc - it only gives prices and qualitative
# capability descriptions ("advanced routing, analytics and AI" for Pro;
# "multi-market... higher-control" for Scale) - so these are an engineering
# judgment call, a sensible progression between the existing Business and
# Enterprise rows, not a value taken from the doc. Only the PRICES (loaded
# separately into PriceCatalogEntry via the real approve/activate service
# functions, not this migration) are the actual approved figures.
# Enterprise's sort_order moves from 3 to 5 to make room for Pro/Scale
# between Business and Enterprise.

PLANS_TABLE = sa.table(
    'plans',
    sa.column('plan_code', sa.String),
    sa.column('name', sa.String),
    sa.column('max_numbers', sa.Integer),
    sa.column('max_team_seats', sa.Integer),
    sa.column('monthly_voice_minutes', sa.Integer),
    sa.column('monthly_video_minutes', sa.Integer),
    sa.column('max_video_participants', sa.Integer),
    sa.column('monthly_ai_summaries', sa.Integer),
    sa.column('trial_days', sa.Integer),
    sa.column('sort_order', sa.Integer),
)


def upgrade() -> None:
    op.execute("UPDATE plans SET sort_order = 5 WHERE plan_code = 'enterprise'")
    op.bulk_insert(
        PLANS_TABLE,
        [
            {
                'plan_code': 'pro', 'name': 'Pro',
                'max_numbers': 15, 'max_team_seats': 35,
                'monthly_voice_minutes': 8000, 'monthly_video_minutes': 4000,
                'max_video_participants': 30, 'monthly_ai_summaries': 1750,
                'trial_days': 0, 'sort_order': 3,
            },
            {
                'plan_code': 'scale', 'name': 'Scale',
                'max_numbers': 25, 'max_team_seats': 60,
                'monthly_voice_minutes': 15000, 'monthly_video_minutes': 7000,
                'max_video_participants': 40, 'monthly_ai_summaries': 3000,
                'trial_days': 0, 'sort_order': 4,
            },
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM plans WHERE plan_code IN ('pro', 'scale')")
    op.execute("UPDATE plans SET sort_order = 3 WHERE plan_code = 'enterprise'")
