"""add STRING value type + string_value column to plan_entitlements

Revision ID: 2aca0e0b665f
Revises: 228807d153c6
Create Date: 2026-08-27 00:00:00.000000

ZL-COM-ENT-001 v3.0 Appendix A adds enum-typed "scope/tier" entitlement
keys (e.g. developer.api.scope: none/limited/standard/advanced/contracted)
that Phase 1-2's BOOLEAN/INTEGER-only value_type can't represent. Schema
change only - no data written here; the v3.0 seed migration that actually
uses this new STRING type is a separate, later migration.

Real production failure this fixed: Postgres refuses to use a freshly
added enum value inside the SAME transaction that added it - true not
just within one migration, but across this whole migration and the next
one too, since alembic/env.py wraps every pending revision applied by a
single `alembic upgrade head` invocation (Render's deploy step) in ONE
shared transaction (`with context.begin_transaction():` around the whole
run, not per-revision). Splitting ADD VALUE into its own earlier-numbered
migration file only helps if that file is deployed and committed as a
fully separate `alembic upgrade head` run - never guaranteed, and not
what actually happened here: this migration and the seed migration that
uses 'STRING' both landed as pending revisions in the same deploy, ran in
the same transaction, and the seed migration's INSERT failed with
psycopg2.errors.UnsafeNewEnumValueUsage. autocommit_block() is Alembic's
documented mechanism for exactly this case (also used for e.g. CREATE
INDEX CONCURRENTLY) - it commits the current transaction, runs the
enclosed statement outside any transaction, then resumes normal
transactional migration afterward.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2aca0e0b665f'
down_revision: Union[str, None] = '228807d153c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE entitlement_value_type_enum ADD VALUE IF NOT EXISTS 'STRING'")
    op.add_column('plan_entitlements', sa.Column('string_value', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('plan_entitlements', 'string_value')
    # Enum VALUE removal isn't supported by Postgres - STRING stays defined
    # even on downgrade, same tradeoff already documented in 2cb8cec0ce38.
