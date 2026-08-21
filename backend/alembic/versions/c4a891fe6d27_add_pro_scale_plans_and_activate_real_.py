"""activate real launch prices (Global Plans, Pricing & Commercial Launch
Standard v1.0, 14 Aug 2026 - "LOCK FOR IMPLEMENTATION")

Revision ID: c4a891fe6d27
Revises: 9b4e2f7a1c63
Create Date: 2026-08-20 00:00:01.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4a891fe6d27'
down_revision: Union[str, None] = '9b4e2f7a1c63'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Pro/Scale Plan rows already exist (4ebb299b8b5f, 2026-08-14) - that
# migration's own docstring notes the prices themselves were deliberately
# left for a separate migration "via the real approve/activate service
# functions" - this is that migration. Nothing here duplicates the Plan
# rows or their entitlement quotas.
#
# Locked monthly USD list prices (doc §1). Annual pricing (~17% off,
# billed upfront) is a distinct billing_period this codebase's Subscription
# model has no field for yet (see that model's docstring - no billing-cycle
# concept beyond a single current_period_start/end pair) - modeling a second
# interval is a bigger change than seeding a price and is left for later;
# only the monthly figures are activated here. Enterprise is explicitly
# "custom"/sales-led per the doc - no self-serve price row for it, same as
# the existing baseline-proposed seed's treatment.
_REAL_PRICES = [
    ("starter", 1299),
    ("business", 1999),
    ("pro", 2999),
    ("scale", 4499),
]

_CATALOG_VERSION = "2026-launch-001"
_PRICE_BOOK_VERSION = "2026-LAUNCH-001"
_APPROVAL_EVIDENCE = (
    "Global Plans, Pricing & Commercial Launch Standard v1.0 (14 Aug 2026), "
    "§1 Executive Decision + §12 Final determination - LOCK FOR IMPLEMENTATION"
)


def upgrade() -> None:
    conn = op.get_bind()
    now = conn.execute(sa.text("SELECT now()")).scalar()
    for plan_code, amount_cents in _REAL_PRICES:
        already_exists = conn.execute(
            sa.text(
                "SELECT 1 FROM price_catalog_entries WHERE plan_code = :p AND catalog_version = :v"
            ),
            {"p": plan_code, "v": _CATALOG_VERSION},
        ).scalar()
        if already_exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO price_catalog_entries (
                    id, plan_code, catalog_version, amount_minor_units, currency_code,
                    status, is_placeholder, price_book_version, market,
                    effective_from, approval_evidence, approved_by, approved_at, created_at
                ) VALUES (
                    :id, :plan_code, :catalog_version, :amount_minor_units, 'USD',
                    'ACTIVE', false, :price_book_version, 'GLOBAL',
                    :now, :approval_evidence, 'system_migration', :now, :now
                )
                """
            ),
            {
                "id": str(uuid.uuid4()), "plan_code": plan_code, "catalog_version": _CATALOG_VERSION,
                "amount_minor_units": amount_cents, "price_book_version": _PRICE_BOOK_VERSION,
                "now": now, "approval_evidence": _APPROVAL_EVIDENCE,
            },
        )
        # No prior ACTIVE entry exists for any of these plan_code+GLOBAL
        # pairs (the only earlier real-priced rows are the still-PROPOSED
        # v1-baseline-proposed set from 7d61853cb8ac) - nothing to retire.


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM price_catalog_entries WHERE catalog_version = :v AND price_book_version = :b"),
        {"v": _CATALOG_VERSION, "b": _PRICE_BOOK_VERSION},
    )
