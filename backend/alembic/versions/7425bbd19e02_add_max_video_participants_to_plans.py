"""add max_video_participants to plans

Revision ID: 7425bbd19e02
Revises: 62980457ed92
Create Date: 2026-08-11 14:21:13.315363

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7425bbd19e02'
down_revision: Union[str, None] = '62980457ed92'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'plans', sa.Column('max_video_participants', sa.Integer(), nullable=False, server_default='8'),
    )
    # Roadmap doc §8 "Phase 1... target up to 8 participants" vs the
    # Architecture doc Phase 3 "larger meetings" tier - free_trial/starter
    # keep the column's own server_default (8); business/enterprise get a
    # real per-room capacity increase.
    op.execute("UPDATE plans SET max_video_participants = 25 WHERE plan_code = 'business'")
    op.execute("UPDATE plans SET max_video_participants = 50 WHERE plan_code = 'enterprise'")


def downgrade() -> None:
    op.drop_column('plans', 'max_video_participants')
