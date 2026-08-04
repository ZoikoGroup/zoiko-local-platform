"""add video summary source type

Revision ID: b1c499e25296
Revises: abc7911db6af
Create Date: 2026-08-04 10:03:06.695615

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c499e25296'
down_revision: Union[str, None] = 'abc7911db6af'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE summary_source_type_enum ADD VALUE IF NOT EXISTS 'VIDEO'")


def downgrade() -> None:
    # Postgres can't drop a single enum value without recreating the whole
    # type - not worth it for a downgrade path (same rationale as the other
    # enum-add migrations in this chain).
    pass
