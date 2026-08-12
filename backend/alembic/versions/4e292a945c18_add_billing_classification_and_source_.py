"""add billing classification and source to accounts

Revision ID: 4e292a945c18
Revises: 81f5e21c6946
Create Date: 2026-08-12 16:27:01.557275

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4e292a945c18'
down_revision: Union[str, None] = '81f5e21c6946'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Note: autogenerate also picked up pre-existing, unrelated drift (an
# 'agent_presence' unique-constraint name and 'calling_rates'
# nullability/constraint-naming mismatch) - deliberately left out, same
# as prior migrations' notes.

# create_type=False on both - the type is created explicitly via a
# race-safe DO block in upgrade() below, not by add_column's own
# auto-create-if-missing behavior.
CLASSIFICATION_ENUM = sa.Enum(
    'COMMERCIAL_STANDALONE', 'COMMERCIAL_BUNDLED', 'LEGACY_MIGRATION', 'PILOT_NON_BILLABLE',
    'PARTNER_SPONSORED', 'INTERNAL', 'DEMO', 'SANDBOX', 'QA_AUTOMATION',
    name='account_billing_classification_enum', create_type=False,
)
SOURCE_ENUM = sa.Enum(
    'DIRECT_ZOIKO_LOCAL', 'ZOIKO_ONE_BUNDLE', 'PARTNER', 'LEGACY',
    name='account_billing_source_enum', create_type=False,
)


def upgrade() -> None:
    # Idempotent CREATE TYPE (Postgres has no CREATE TYPE IF NOT EXISTS) -
    # checkfirst=True alone isn't safe against another concurrent
    # migration run also racing to create the same type.
    op.execute(
        "DO $$ BEGIN CREATE TYPE account_billing_classification_enum AS ENUM "
        "('COMMERCIAL_STANDALONE', 'COMMERCIAL_BUNDLED', 'LEGACY_MIGRATION', 'PILOT_NON_BILLABLE', "
        "'PARTNER_SPONSORED', 'INTERNAL', 'DEMO', 'SANDBOX', 'QA_AUTOMATION'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    op.execute(
        "DO $$ BEGIN CREATE TYPE account_billing_source_enum AS ENUM "
        "('DIRECT_ZOIKO_LOCAL', 'ZOIKO_ONE_BUNDLE', 'PARTNER', 'LEGACY'); "
        "EXCEPTION WHEN duplicate_object THEN null; END $$;"
    )
    # server_default so this backfills every existing account (there are
    # real ones already) as COMMERCIAL_STANDALONE/DIRECT_ZOIKO_LOCAL - the
    # correct classification for every account created through the normal
    # public signup path to date (see Account's docstring: no other class
    # has a public signup path).
    op.add_column(
        'accounts',
        sa.Column('billing_classification', CLASSIFICATION_ENUM, nullable=False, server_default='COMMERCIAL_STANDALONE'),
    )
    op.add_column(
        'accounts',
        sa.Column('billing_source', SOURCE_ENUM, nullable=False, server_default='DIRECT_ZOIKO_LOCAL'),
    )

    # Same segregation-of-duties bar as other billing-adjacent capabilities -
    # misclassifying an account either dodges real billing or exposes a
    # demo/test account to live charges.
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table, [{'id': str(uuid.uuid4()), 'capability': 'staff.manage_billing_classification', 'role': 'SUPER_ADMIN'}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'staff.manage_billing_classification'")
    op.drop_column('accounts', 'billing_source')
    op.drop_column('accounts', 'billing_classification')
    op.execute("DROP TYPE IF EXISTS account_billing_source_enum")
    op.execute("DROP TYPE IF EXISTS account_billing_classification_enum")
