"""add number and AI usage rate cards, plan AI receptionist minutes

Revision ID: 61bc6e50e6db
Revises: 4ebb299b8b5f
Create Date: 2026-08-17 11:46:13.561399

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = '61bc6e50e6db'
down_revision: Union[str, None] = '4ebb299b8b5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Global Plans, Pricing & Commercial Launch Standard doc §5.1/§5.3 - real,
# doc-approved baseline figures (is_placeholder=False), not invented test
# numbers like app.usage.models.CallingRate's seeded rows. "XX" is the same
# DEFAULT_RATE_COUNTRY fallback CallingRate already uses.
_NUMBER_RATE_CENTS = 499  # "Additional standard local number: From $4.99/month"
_AI_OVERAGE_CENTS_PER_MINUTE = 39  # "$0.39/minute thereafter"


def upgrade() -> None:
    op.create_table(
        "number_rates",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("number_type", sa.String(length=30), nullable=False, server_default="local"),
        sa.Column("recurring_price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("is_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("country", "number_type", name="uq_number_rate_country_type"),
    )
    op.create_index("ix_number_rates_country", "number_rates", ["country"])

    op.create_table(
        "ai_usage_rates",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("overage_price_cents_per_minute", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("is_placeholder", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.add_column(
        "plans",
        sa.Column("included_ai_receptionist_minutes", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute("UPDATE plans SET included_ai_receptionist_minutes = 50 WHERE plan_code = 'pro'")
    op.execute("UPDATE plans SET included_ai_receptionist_minutes = 150 WHERE plan_code = 'scale'")

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
                "id": str(uuid.uuid4()), "country": "XX", "number_type": "local",
                "recurring_price_cents": _NUMBER_RATE_CENTS, "currency": "USD", "is_placeholder": False,
            },
        ],
    )

    ai_usage_rates_table = sa.table(
        "ai_usage_rates",
        sa.column("id", sa.String),
        sa.column("overage_price_cents_per_minute", sa.Integer),
        sa.column("currency", sa.String),
        sa.column("is_placeholder", sa.Boolean),
    )
    op.bulk_insert(
        ai_usage_rates_table,
        [
            {
                "id": str(uuid.uuid4()), "overage_price_cents_per_minute": _AI_OVERAGE_CENTS_PER_MINUTE,
                "currency": "USD", "is_placeholder": False,
            },
        ],
    )


def downgrade() -> None:
    op.drop_column("plans", "included_ai_receptionist_minutes")
    op.drop_table("ai_usage_rates")
    op.drop_index("ix_number_rates_country", table_name="number_rates")
    op.drop_table("number_rates")
