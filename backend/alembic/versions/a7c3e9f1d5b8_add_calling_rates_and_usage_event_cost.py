"""add calling rates and usage event estimated cost

Revision ID: a7c3e9f1d5b8
Revises: f2a7c583d9e1
Create Date: 2026-08-06 17:45:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'a7c3e9f1d5b8'
down_revision: Union[str, None] = 'f2a7c583d9e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Placeholder launch rates, not a real carrier rate card - see
# app.usage.models.CallingRate's docstring. "XX" is the DEFAULT_RATE_COUNTRY
# fallback applied to any curated country without its own explicit row.
_SEED_RATES = [
    ("US", 1), ("CA", 1), ("GB", 2), ("AU", 3), ("DE", 2), ("FR", 2), ("IN", 4), ("SG", 3),
    ("XX", 5),
]


def upgrade() -> None:
    op.create_table(
        "calling_rates",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("country", sa.String(length=2), nullable=False, unique=True),
        sa.Column("price_per_minute_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_calling_rates_country", "calling_rates", ["country"])

    calling_rates_table = sa.table(
        "calling_rates",
        sa.column("id", sa.String),
        sa.column("country", sa.String),
        sa.column("price_per_minute_cents", sa.Integer),
        sa.column("currency", sa.String),
    )
    op.bulk_insert(
        calling_rates_table,
        [
            {"id": str(uuid.uuid4()), "country": country, "price_per_minute_cents": cents, "currency": "USD"}
            for country, cents in _SEED_RATES
        ],
    )

    op.add_column("usage_events", sa.Column("estimated_cost_cents", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("usage_events", "estimated_cost_cents")
    op.drop_index("ix_calling_rates_country", table_name="calling_rates")
    op.drop_table("calling_rates")
