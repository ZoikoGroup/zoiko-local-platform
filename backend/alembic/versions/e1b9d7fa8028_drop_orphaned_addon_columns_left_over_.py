"""drop orphaned addon columns left over from resolving a duplicate ai receptionist addon feature

Revision ID: e1b9d7fa8028
Revises: e45c64daa63a
Create Date: 2026-08-21 10:46:48.066841

Two branches (this one and anilupdated) independently built the AI
Receptionist add-on toggle/pricing. Reconciling them kept anilupdated's
design (a proper versioned ai_receptionist_addon_rates table, subscriptions.
ai_receptionist_addon_enabled) and dropped this branch's redundant
ai_usage_rates.addon_monthly_price_cents/addon_included_minutes and
subscriptions.ai_receptionist_addon_active from the SQLAlchemy models -
but 4ec152435b05 had already run against this real database (adding those
3 columns) before its source was edited to stop adding them. Editing an
already-applied migration's file doesn't retroactively undo what it did to
a real database - this migration is the actual DROP, confirmed live via
`alembic check` after the merge (which is exactly what caught this drift).

Guarded with checkfirst=True (2026-08-24): a database that never had the
old, pre-edit version of 4ec152435b05 run against it (any fresh database
migrating from scratch - confirmed live against a brand-new Neon instance)
never had these 3 columns to begin with, so an unconditional drop_column
fails with UndefinedColumn. Only databases that ran the old 4ec152435b05
(this repo's shared local dev DB) actually need this drop; everyone else
needs it to be a no-op.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1b9d7fa8028'
down_revision: Union[str, None] = 'e45c64daa63a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    ai_usage_rates_columns = {c['name'] for c in inspector.get_columns('ai_usage_rates')}
    subscriptions_columns = {c['name'] for c in inspector.get_columns('subscriptions')}

    if 'addon_monthly_price_cents' in ai_usage_rates_columns:
        op.drop_column('ai_usage_rates', 'addon_monthly_price_cents')
    if 'addon_included_minutes' in ai_usage_rates_columns:
        op.drop_column('ai_usage_rates', 'addon_included_minutes')
    if 'ai_receptionist_addon_active' in subscriptions_columns:
        op.drop_column('subscriptions', 'ai_receptionist_addon_active')


def downgrade() -> None:
    op.add_column('subscriptions', sa.Column('ai_receptionist_addon_active', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('ai_usage_rates', sa.Column('addon_included_minutes', sa.Integer(), server_default='100', nullable=False))
    op.add_column('ai_usage_rates', sa.Column('addon_monthly_price_cents', sa.Integer(), server_default='2900', nullable=False))
