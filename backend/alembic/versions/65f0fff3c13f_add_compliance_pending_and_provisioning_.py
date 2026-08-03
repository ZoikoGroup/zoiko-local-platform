"""add compliance_pending and provisioning number states, cancelled_at

Revision ID: 65f0fff3c13f
Revises: 4561d5bcf176
Create Date: 2026-08-03 20:01:34.631360

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '65f0fff3c13f'
down_revision: Union[str, None] = '4561d5bcf176'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE phone_number_status_enum ADD VALUE IF NOT EXISTS 'COMPLIANCE_PENDING'")
    op.execute("ALTER TYPE phone_number_status_enum ADD VALUE IF NOT EXISTS 'PROVISIONING'")
    op.add_column("phone_numbers", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    # Postgres can't drop a single enum value without recreating the whole
    # type (and every column/index/default that depends on it) - not worth
    # it for a downgrade path. The column drop is safe and reversible.
    op.drop_column("phone_numbers", "cancelled_at")
