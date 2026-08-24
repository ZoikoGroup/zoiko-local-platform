"""add recording_started_at to video_sessions for stale recording detection

Revision ID: bbbec1ba005e
Revises: 27b720ab83e5
Create Date: 2026-08-21 14:43:33.328387

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'bbbec1ba005e'
down_revision: Union[str, None] = '27b720ab83e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('video_sessions', sa.Column('recording_started_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('video_sessions', 'recording_started_at')
