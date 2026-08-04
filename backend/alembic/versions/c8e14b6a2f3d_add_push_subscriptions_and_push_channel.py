"""add push_subscriptions table and push notification channel

Revision ID: c8e14b6a2f3d
Revises: a3f5c9d2e148
Create Date: 2026-08-04 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c8e14b6a2f3d'
down_revision: Union[str, None] = 'a3f5c9d2e148'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "push_subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("p256dh", sa.String(length=255), nullable=False),
        sa.Column("auth", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_push_subscriptions_account_id", "push_subscriptions", ["account_id"])
    op.create_unique_constraint("uq_push_subscriptions_endpoint", "push_subscriptions", ["endpoint"])

    op.execute("ALTER TYPE notification_channel_enum ADD VALUE IF NOT EXISTS 'PUSH'")

    op.add_column(
        "notification_deliveries",
        sa.Column(
            "push_subscription_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("push_subscriptions.id", ondelete="SET NULL"), nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("notification_deliveries", "push_subscription_id")
    # Postgres can't drop a single enum value without recreating the whole
    # type - not worth it for a downgrade path (same rationale as the other
    # enum-add migrations in this chain).
    op.drop_constraint("uq_push_subscriptions_endpoint", "push_subscriptions", type_="unique")
    op.drop_index("ix_push_subscriptions_account_id", table_name="push_subscriptions")
    op.drop_table("push_subscriptions")
