"""retire duplicate active prices from the anilupdated 2026-launch-001 catalog version

Revision ID: e45c64daa63a
Revises: f6e5a8845023
Create Date: 2026-08-21 10:38:23.491930

c4a891fe6d27 ("activate real launch prices") inserted its own
price_catalog_entries rows directly as status=ACTIVE via raw SQL, bypassing
activate_price_catalog_entry's "retire whatever was previously ACTIVE for
this plan_code/market/billing_period" logic - it had no way to know that
logic existed, being written on a separate branch. The result: 4 plans
(starter/business/pro/scale monthly) ended up with TWO simultaneously
ACTIVE rows - its own 2026-launch-001/2026-LAUNCH-001 catalog_version/
price_book_version, alongside this repo's actually-approved
v2-2026-global-launch/2026-08-14-global-launch-usd rows (real product-owner
signoff recorded on 2026-08-20 - see CLAUDE.md). Confirmed live after
running the merge migration: both catalog versions carry the identical
dollar amounts, so no customer was ever charged wrong - this is a data-
integrity fix (restoring the "one ACTIVE row per plan/market/period"
invariant get_active_price_catalog_entry and every other query in this
codebase assumes), not a price correction.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e45c64daa63a'
down_revision: Union[str, None] = 'f6e5a8845023'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DUPLICATE_CATALOG_VERSION = "2026-launch-001"


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE price_catalog_entries SET status = 'RETIRED' "
            "WHERE catalog_version = :catalog_version AND status = 'ACTIVE'"
        ),
        {"catalog_version": _DUPLICATE_CATALOG_VERSION},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE price_catalog_entries SET status = 'ACTIVE' "
            "WHERE catalog_version = :catalog_version AND status = 'RETIRED'"
        ),
        {"catalog_version": _DUPLICATE_CATALOG_VERSION},
    )
