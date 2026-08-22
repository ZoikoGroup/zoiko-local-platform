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

IF EXISTS/IF NOT EXISTS on every column op below: this migration is only
meaningful for a database whose history actually ran the OLD
(pre-edit) 4ec152435b05 and therefore has these 3 columns to drop. A
from-scratch bootstrap (a fresh dev DB, CI, or anyone cloning this repo
today) runs 4ec152435b05's CURRENT source, which never creates them in
the first place - this DROP must not fail just because there was nothing
to drop in that case.
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
    op.execute("ALTER TABLE ai_usage_rates DROP COLUMN IF EXISTS addon_monthly_price_cents")
    op.execute("ALTER TABLE ai_usage_rates DROP COLUMN IF EXISTS addon_included_minutes")
    op.execute("ALTER TABLE subscriptions DROP COLUMN IF EXISTS ai_receptionist_addon_active")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS ai_receptionist_addon_active "
        "boolean NOT NULL DEFAULT false"
    )
    op.execute(
        "ALTER TABLE ai_usage_rates ADD COLUMN IF NOT EXISTS addon_included_minutes "
        "integer NOT NULL DEFAULT 100"
    )
    op.execute(
        "ALTER TABLE ai_usage_rates ADD COLUMN IF NOT EXISTS addon_monthly_price_cents "
        "integer NOT NULL DEFAULT 2900"
    )
