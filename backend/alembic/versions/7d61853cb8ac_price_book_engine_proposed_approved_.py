"""price book engine: PROPOSED/APPROVED/ACTIVE/RETIRED + versioned fields

Revision ID: 7d61853cb8ac
Revises: 4e50ef88c70b
Create Date: 2026-08-13 15:10:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d61853cb8ac'
down_revision: Union[str, None] = '4e50ef88c70b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Production Readiness & Go-Live Decision Standard §2.3/Table 8 -
    # rename the existing DRAFT value to PROPOSED (same state, doc's exact
    # vocabulary) and add ACTIVE/RETIRED as new states in the lifecycle.
    # NOTE: this enum's actual stored labels are the Python enum MEMBER
    # NAMES ('DRAFT', 'APPROVED'), not their lowercase .value strings -
    # SQLAlchemy's Enum(SomeEnum) maps by .name by default (confirmed via
    # pg_enum against the live DB, not guessed).
    op.execute("ALTER TYPE catalog_entry_status_enum RENAME VALUE 'DRAFT' TO 'PROPOSED'")
    op.execute("ALTER TYPE catalog_entry_status_enum ADD VALUE 'ACTIVE'")
    op.execute("ALTER TYPE catalog_entry_status_enum ADD VALUE 'RETIRED'")

    op.add_column('price_catalog_entries', sa.Column('price_book_version', sa.String(length=50), nullable=True))
    op.add_column(
        'price_catalog_entries',
        sa.Column('market', sa.String(length=10), nullable=False, server_default='GLOBAL'),
    )
    op.add_column('price_catalog_entries', sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True))
    op.add_column('price_catalog_entries', sa.Column('effective_to', sa.DateTime(timezone=True), nullable=True))
    op.add_column('price_catalog_entries', sa.Column('approval_evidence', sa.String(length=255), nullable=True))
    op.create_index(
        op.f('ix_price_catalog_entries_price_book_version'), 'price_catalog_entries', ['price_book_version'],
        unique=False,
    )

    # Table 4 ("Refined position - Pricing"): "Keep prior figures as
    # PROPOSED seed configuration only until Commercial/Finance signs the
    # production price book." These are the doc's own previously-existing
    # baseline figures (US$15 starter / US$29 business), NOT fake test
    # data (is_placeholder=False - they're a real candidate price, just
    # not yet ratified) and NOT chargeable (status defaults to PROPOSED,
    # and run_billing_cycle now requires ACTIVE outside development).
    # Enterprise is explicitly "custom" per the doc - no fixed figure to
    # seed. free_trial is seeded at 0 for completeness, matching its
    # existing zero-cost intent.
    entries_table = sa.table(
        'price_catalog_entries',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('plan_code', sa.String),
        sa.column('catalog_version', sa.String),
        sa.column('amount_minor_units', sa.Integer),
        sa.column('currency_code', sa.String),
        sa.column('status', sa.String),
        sa.column('is_placeholder', sa.Boolean),
        sa.column('price_book_version', sa.String),
        sa.column('market', sa.String),
    )
    op.bulk_insert(
        entries_table,
        [
            {
                'id': str(uuid.uuid4()), 'plan_code': plan_code,
                'catalog_version': 'v1-baseline-proposed', 'amount_minor_units': amount,
                'currency_code': 'USD', 'status': 'PROPOSED', 'is_placeholder': False,
                'price_book_version': '2026-BASELINE-PROPOSED-001', 'market': 'GLOBAL',
            }
            for plan_code, amount in [
                ('free_trial', 0), ('starter', 1500), ('business', 2900),
            ]
        ],
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM price_catalog_entries WHERE catalog_version = 'v1-baseline-proposed' "
        "AND price_book_version = '2026-BASELINE-PROPOSED-001'"
    )
    op.drop_index(op.f('ix_price_catalog_entries_price_book_version'), table_name='price_catalog_entries')
    op.drop_column('price_catalog_entries', 'approval_evidence')
    op.drop_column('price_catalog_entries', 'effective_to')
    op.drop_column('price_catalog_entries', 'effective_from')
    op.drop_column('price_catalog_entries', 'market')
    op.drop_column('price_catalog_entries', 'price_book_version')
    # Postgres has no ALTER TYPE ... DROP VALUE - values added by this
    # migration are left in place on downgrade, same limitation noted by
    # every other enum-extending migration in this codebase.
    op.execute("ALTER TYPE catalog_entry_status_enum RENAME VALUE 'PROPOSED' TO 'DRAFT'")
