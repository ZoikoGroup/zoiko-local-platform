"""revert price catalog entries to proposed pending real finance signoff

Revision ID: 679061a79e97
Revises: 4ec152435b05
Create Date: 2026-08-19 10:28:35.467259

Production Readiness Standard doc, Rule of Authority (§1): "Engineering may
not self-ratify pricing... Those require named business/legal/finance
sign-offs recorded in the release evidence." And §2.1's explicit direction:
"Implement the plan/price engine now using the prior baseline as PROPOSED
configuration. Do not expose or charge those figures in paid production
until Commercial + Finance validate unit economics and issue the ACTIVE
price-book version."

The 8 real (is_placeholder=False) price_catalog_entries for price_book_
version '2026-08-14-global-launch-usd' (4 monthly + 4 annual) were marked
ACTIVE by engineering, not by a real Commercial/Finance approval -
approved_by literally says "engineering:price-catalog-load-...", and
approval_evidence (the doc's own required "Commercial/Finance approval ID
and change authority" field) was never populated. This has a real
behavioral consequence, not just a labeling one: run_billing_cycle's own
gate only blocks charging a non-ACTIVE/non-APPROVED entry OUTSIDE
development (see that function's docstring) - so as currently marked,
these prices would be treated as real, chargeable, ratified prices the
moment this system runs in a non-development environment, which is
precisely what the Rule of Authority prohibits.

This migration reverts them to PROPOSED and clears approved_by/approved_at
(those fields specifically mean "who and when a real approval happened" -
leaving stale values there after acknowledging no real approval occurred
would be worse than leaving them empty). Customer-facing price DISPLAY is
unaffected: get_active_price_catalog_entry's own documented fallback
("nothing ACTIVE yet - use the most recently created entry regardless of
status") already exists for exactly this dev/test-convenience case, so the
same prices keep showing in the UI. Only real production chargeability
changes. Re-activating for real requires a genuine Commercial+Finance
sign-off, recorded via approval_evidence, then approve_price_catalog_entry
+ activate_price_catalog_entry - not this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '679061a79e97'
down_revision: Union[str, None] = '4ec152435b05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRICE_BOOK_VERSION = '2026-08-14-global-launch-usd'


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE price_catalog_entries
            SET status = 'PROPOSED', approved_by = NULL, approved_at = NULL
            WHERE price_book_version = :price_book_version
              AND status = 'ACTIVE'
              AND is_placeholder = false
            """
        ).bindparams(price_book_version=_PRICE_BOOK_VERSION)
    )
    # The 4 monthly rows (set by an earlier session, before this one) also
    # had approval_evidence = "...docx - executive approval, section
    # 1/10..." - citing the doc ITSELF as "executive approval" is the exact
    # same self-ratification problem this migration is fixing at the
    # status level: no named executive actually approved anything, the
    # doc's suggested figures were just cited as if that were equivalent.
    # Cleared for the same honesty reason approved_by/approved_at are
    # cleared above - a real approval_evidence value must be a real
    # Commercial/Finance approval ID, not a document citation.
    op.execute(
        sa.text(
            """
            UPDATE price_catalog_entries
            SET approval_evidence = NULL
            WHERE price_book_version = :price_book_version
              AND approval_evidence LIKE '%executive approval%'
            """
        ).bindparams(price_book_version=_PRICE_BOOK_VERSION)
    )


def downgrade() -> None:
    # Restores the exact prior self-activated state (including the
    # engineering approved_by attribution) - a downgrade should reverse
    # this migration's effect precisely, not express an opinion about
    # whether that prior state was correct.
    op.execute(
        sa.text(
            """
            UPDATE price_catalog_entries
            SET status = 'ACTIVE',
                approved_by = CASE
                    WHEN billing_period = 'ANNUAL' THEN 'engineering:price-catalog-load-2026-08-19-annual'
                    ELSE 'engineering:price-catalog-load-2026-08-14'
                END,
                approved_at = created_at,
                approval_evidence = CASE
                    WHEN billing_period = 'MONTHLY' THEN
                        'Zoiko_Local_Global_Plans_Pricing_Commercial_Launch.docx - executive approval, '
                        || 'section 1/10, retrieved 2026-08-14'
                    ELSE approval_evidence
                END
            WHERE price_book_version = :price_book_version
              AND status = 'PROPOSED'
              AND is_placeholder = false
            """
        ).bindparams(price_book_version=_PRICE_BOOK_VERSION)
    )
