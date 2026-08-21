"""add annual billing, ai receptionist addon, and market activation signoff fields

Revision ID: 4ec152435b05
Revises: 66711565c20f
Create Date: 2026-08-19 09:17:53.098521

Global Plans, Pricing & Commercial Launch doc gaps closed here:
- Annual billing didn't exist at all (no billing_period dimension anywhere)
  - adds it to both price_catalog_entries (a real, distinct catalog row per
    period, same Class-A-immutable discipline as every other price here,
    not a computed monthly*12*0.83) and subscriptions (which cadence this
    customer is actually on). Seeds the doc's own real annual figures
    ($129/$199/$299/$449) as ACTIVE alongside the existing monthly rows.
- Market Activation Registry §6.2 "PAID_OPEN only after ... named sign-off"
  - legal_signoff_reference/legal_signoff_by give set_market_activation_
    status somewhere real to record that evidence going forward. Left NULL
    on existing rows deliberately - backfilling a fake reviewer name for
    this project's 8 already-PAID_OPEN countries would be exactly the kind
    of invented approval this doc prohibits.

NOTE ON SCOPE: autogenerate also detected 4 columns on supported_countries
(activation_state/activation_changed_at/activation_notes/
activation_changed_by) that exist in the live DB but not in the current
SQLAlchemy model at all - pre-existing schema drift from an earlier/
parallel Market Activation Registry implementation (a different enum name,
market_activation_state_enum, vs the current model's
market_activation_status_enum), unrelated to this change. Deliberately
NOT touched here - dropping them is a separate decision for whoever owns
that drift, not a side effect of this migration.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '4ec152435b05'
down_revision: Union[str, None] = '66711565c20f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ANNUAL_PRICES_CENTS = {
    'starter': 12900,
    'business': 19900,
    'pro': 29900,
    'scale': 44900,
}


def upgrade() -> None:
    billing_period_enum = postgresql.ENUM('MONTHLY', 'ANNUAL', name='billing_period_enum')
    billing_period_enum.create(op.get_bind(), checkfirst=True)

    op.add_column('price_catalog_entries', sa.Column('billing_period', billing_period_enum, server_default='MONTHLY', nullable=False))
    op.drop_constraint('uq_price_catalog_entry_plan_version', 'price_catalog_entries', type_='unique')
    op.create_unique_constraint('uq_price_catalog_entry_plan_version_period', 'price_catalog_entries', ['plan_code', 'catalog_version', 'billing_period'])
    op.add_column('subscriptions', sa.Column('billing_period', billing_period_enum, server_default='MONTHLY', nullable=False))
    op.add_column('supported_countries', sa.Column('legal_signoff_reference', sa.String(length=100), nullable=True))
    op.add_column('supported_countries', sa.Column('legal_signoff_by', sa.String(length=100), nullable=True))

    conn = op.get_bind()
    for plan_code, amount_cents in _ANNUAL_PRICES_CENTS.items():
        conn.execute(
            sa.text(
                """
                INSERT INTO price_catalog_entries
                    (id, plan_code, catalog_version, billing_period, amount_minor_units, currency_code,
                     status, is_placeholder, price_book_version, market, approved_by, approved_at)
                SELECT gen_random_uuid(), :plan_code, 'v2-2026-global-launch', 'ANNUAL', :amount_cents, 'USD',
                       'ACTIVE', false, '2026-08-14-global-launch-usd', 'GLOBAL',
                       'engineering:price-catalog-load-2026-08-19-annual', now()
                WHERE EXISTS (SELECT 1 FROM plans WHERE plan_code = :plan_code)
                ON CONFLICT DO NOTHING
                """
            ),
            {"plan_code": plan_code, "amount_cents": amount_cents},
        )


def downgrade() -> None:
    op.execute("DELETE FROM price_catalog_entries WHERE price_book_version = '2026-08-14-global-launch-usd' AND billing_period = 'ANNUAL'")
    op.drop_column('supported_countries', 'legal_signoff_by')
    op.drop_column('supported_countries', 'legal_signoff_reference')
    op.drop_column('subscriptions', 'billing_period')
    op.drop_constraint('uq_price_catalog_entry_plan_version_period', 'price_catalog_entries', type_='unique')
    op.create_unique_constraint('uq_price_catalog_entry_plan_version', 'price_catalog_entries', ['plan_code', 'catalog_version'])
    op.drop_column('price_catalog_entries', 'billing_period')
    postgresql.ENUM(name='billing_period_enum').drop(op.get_bind(), checkfirst=True)
