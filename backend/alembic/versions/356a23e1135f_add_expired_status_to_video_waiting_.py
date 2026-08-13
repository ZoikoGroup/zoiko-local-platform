"""add expired status to video waiting guest

Revision ID: 356a23e1135f
Revises: e796f9fa547f
Create Date: 2026-08-11 13:28:49.297234

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '356a23e1135f'
down_revision: Union[str, None] = 'e796f9fa547f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE video_waiting_guest_status_enum ADD VALUE IF NOT EXISTS 'EXPIRED'")


def downgrade() -> None:
    # No DROP VALUE in Postgres - EXPIRED stays defined even on downgrade,
    # same tradeoff every other enum-extending migration in this codebase
    # already accepts.
    pass
