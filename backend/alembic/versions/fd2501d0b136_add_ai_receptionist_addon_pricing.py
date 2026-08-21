"""add ai receptionist addon pricing (Pricing doc §5.3, LOCK FOR
IMPLEMENTATION)

Revision ID: fd2501d0b136
Revises: e7c2b6f184a9
Create Date: 2026-08-20 00:00:04.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'fd2501d0b136'
down_revision: Union[str, None] = 'e7c2b6f184a9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CATALOG_VERSION = "2026-launch-001"
_PRICE_BOOK_VERSION = "2026-LAUNCH-001"
_APPROVAL_EVIDENCE = (
    "Global Plans, Pricing & Commercial Launch Standard v1.0 (14 Aug 2026), "
    "§5.3 AI Receptionist - LOCK FOR IMPLEMENTATION"
)


def upgrade() -> None:
    # catalog_entry_status_enum already exists (created for
    # price_catalog_entries, e0f8f78c88dd) - reused here, not recreated.
    op.create_table(
        "ai_receptionist_addon_rates",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("catalog_version", sa.String(length=50), nullable=False, unique=True),
        sa.Column("monthly_price_minor_units", sa.Integer(), nullable=False),
        sa.Column("included_minutes", sa.Integer(), nullable=False),
        sa.Column("overage_rate_minor_units_per_minute", sa.Integer(), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column(
            "status",
            postgresql.ENUM(name="catalog_entry_status_enum", create_type=False),
            nullable=False,
        ),
        sa.Column("is_placeholder", sa.Boolean(), nullable=False),
        sa.Column("price_book_version", sa.String(length=50), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_evidence", sa.String(length=255), nullable=True),
        sa.Column("approved_by", sa.String(length=100), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_ai_receptionist_addon_rates_price_book_version",
        "ai_receptionist_addon_rates", ["price_book_version"],
    )

    op.add_column(
        "subscriptions",
        sa.Column("ai_receptionist_addon_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Seeding plans.included_ai_receptionist_minutes (Pro=50/Scale=150) is
    # deliberately NOT done here - that column is added by 61bc6e50e6db, on
    # an unrelated sibling branch this migration has no ordering guarantee
    # against (both are just children of a shared ancestor). Done instead in
    # the merge migration that reconciles both branches, the first point
    # where both are guaranteed to have already run - see its own comment.

    conn = op.get_bind()
    now = conn.execute(sa.text("SELECT now()")).scalar()
    conn.execute(
        sa.text(
            """
            INSERT INTO ai_receptionist_addon_rates (
                id, catalog_version, monthly_price_minor_units, included_minutes,
                overage_rate_minor_units_per_minute, currency_code, status, is_placeholder,
                price_book_version, effective_from, approval_evidence, approved_by, approved_at, created_at
            ) VALUES (
                :id, :catalog_version, 2900, 100,
                39, 'USD', 'ACTIVE', false,
                :price_book_version, :now, :approval_evidence, 'system_migration', :now, :now
            )
            """
        ),
        {
            "id": str(uuid.uuid4()), "catalog_version": _CATALOG_VERSION,
            "price_book_version": _PRICE_BOOK_VERSION, "now": now, "approval_evidence": _APPROVAL_EVIDENCE,
        },
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "ai_receptionist_addon_enabled")
    op.drop_index("ix_ai_receptionist_addon_rates_price_book_version", table_name="ai_receptionist_addon_rates")
    op.drop_table("ai_receptionist_addon_rates")
