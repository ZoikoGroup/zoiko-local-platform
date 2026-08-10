"""add next_renewal_at to phone numbers

Revision ID: f2a7c583d9e1
Revises: de8172a21362
Create Date: 2026-08-06 17:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f2a7c583d9e1'
down_revision: Union[str, None] = 'de8172a21362'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("phone_numbers", sa.Column("next_renewal_at", sa.DateTime(timezone=True), nullable=True))
    # Backfill existing ACTIVE numbers so the renewal worklist has a
    # starting point instead of every currently-owned number silently
    # never coming up for renewal - 30 days from now, matching
    # RENEWAL_PERIOD_DAYS, so nothing appears artificially overdue on day one.
    op.execute(sa.text("""
        UPDATE phone_numbers SET next_renewal_at = now() + interval '30 days'
        WHERE status = 'ACTIVE'
    """))


def downgrade() -> None:
    op.drop_column("phone_numbers", "next_renewal_at")
