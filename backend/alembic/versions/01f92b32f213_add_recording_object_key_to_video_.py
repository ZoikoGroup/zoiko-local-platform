"""add recording_object_key to video_sessions for human readable recording filenames

Revision ID: 01f92b32f213
Revises: bbbec1ba005e
Create Date: 2026-08-21 15:03:48.196705

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '01f92b32f213'
down_revision: Union[str, None] = 'bbbec1ba005e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('video_sessions', sa.Column('recording_object_key', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('video_sessions', 'recording_object_key')
