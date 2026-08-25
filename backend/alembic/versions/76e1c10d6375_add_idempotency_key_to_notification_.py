"""add idempotency key to notification deliveries

Revision ID: 76e1c10d6375
Revises: 922668045791
Create Date: 2026-08-22 16:16:32.776495

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '76e1c10d6375'
down_revision: Union[str, None] = '922668045791'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("notification_deliveries", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.create_index(
        op.f("ix_notification_deliveries_idempotency_key"), "notification_deliveries", ["idempotency_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_notification_deliveries_idempotency_key"), table_name="notification_deliveries")
    op.drop_column("notification_deliveries", "idempotency_key")
