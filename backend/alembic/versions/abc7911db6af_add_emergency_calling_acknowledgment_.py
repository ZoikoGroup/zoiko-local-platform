"""add emergency calling acknowledgment consent type

Revision ID: abc7911db6af
Revises: 07e9f893394a
Create Date: 2026-08-03 22:56:05.704219

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'abc7911db6af'
down_revision: Union[str, None] = '07e9f893394a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE consent_type_enum ADD VALUE IF NOT EXISTS 'EMERGENCY_CALLING_ACKNOWLEDGED'")


def downgrade() -> None:
    # Postgres can't drop a single enum value without recreating the whole
    # type - not worth it for a downgrade path (same rationale as the
    # phone_number_status_enum migration).
    pass
