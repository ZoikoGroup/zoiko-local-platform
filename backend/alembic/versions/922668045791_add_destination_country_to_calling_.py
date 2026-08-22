"""add destination country to calling rates and seed placeholder per country number rates

Revision ID: 922668045791
Revises: db8d0f0b2e05
Create Date: 2026-08-22 11:43:06.791670

Two independent, small gaps:

1. calling_rates has no way to represent a destination-specific rate at
   all - only ever origin-only (see CallingRate's own docstring on why:
   no real E.164-to-country parser exists yet). Adds the column and widens
   the unique constraint from (country) to (country, destination_country)
   so a future destination-aware rate can coexist with today's origin-only
   ones. Nothing reads this column yet - schema readiness only, not a
   pricing change.
2. number_rates only had the single DEFAULT_RATE_COUNTRY ("XX") fallback
   row (61bc6e50e6db) - every real country fell through to that one
   doc-approved $4.99 baseline. Seeds that same baseline figure under a
   handful of real country codes instead, explicitly marked
   is_placeholder=True (unlike the real XX baseline row) since these are
   not per-country business decisions, just the one known figure applied
   more granularly - a real per-country rate card is still not modeled.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '922668045791'
down_revision: Union[str, None] = 'db8d0f0b2e05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NUMBER_RATE_CENTS = 499  # same "$4.99/month" baseline as the XX fallback row (61bc6e50e6db)
_PLACEHOLDER_COUNTRIES = ["US", "CA", "GB", "AU", "DE", "FR"]


def upgrade() -> None:
    op.drop_constraint("calling_rates_country_key", "calling_rates", type_="unique")
    op.add_column("calling_rates", sa.Column("destination_country", sa.String(length=2), nullable=True))
    op.create_index(op.f("ix_calling_rates_destination_country"), "calling_rates", ["destination_country"])
    op.create_unique_constraint(
        "uq_calling_rate_country_destination", "calling_rates", ["country", "destination_country"]
    )

    number_rates_table = sa.table(
        "number_rates",
        sa.column("id", sa.String),
        sa.column("country", sa.String),
        sa.column("number_type", sa.String),
        sa.column("recurring_price_cents", sa.Integer),
        sa.column("currency", sa.String),
        sa.column("is_placeholder", sa.Boolean),
    )
    op.bulk_insert(
        number_rates_table,
        [
            {
                "id": str(uuid.uuid4()), "country": country, "number_type": "local",
                "recurring_price_cents": _NUMBER_RATE_CENTS, "currency": "USD", "is_placeholder": True,
            }
            for country in _PLACEHOLDER_COUNTRIES
        ],
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM number_rates WHERE country = ANY(:countries) AND is_placeholder = true"),
        {"countries": _PLACEHOLDER_COUNTRIES},
    )
    op.drop_constraint("uq_calling_rate_country_destination", "calling_rates", type_="unique")
    op.drop_index(op.f("ix_calling_rates_destination_country"), table_name="calling_rates")
    op.drop_column("calling_rates", "destination_country")
    op.create_unique_constraint("calling_rates_country_key", "calling_rates", ["country"])
