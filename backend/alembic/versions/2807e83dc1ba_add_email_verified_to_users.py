"""add email_verified to users

Revision ID: 2807e83dc1ba
Revises: 7546752c1585
Create Date: 2026-08-31 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2807e83dc1ba'
down_revision: Union[str, None] = '7546752c1585'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Production Readiness Standard doc §5 "Identity" trial-abuse control -
    # server_default=true backfills every EXISTING user as verified (no
    # retroactive lockout for real customers who already signed up); new
    # signups get email_verified=False from the ORM's own default=False
    # (create_account_with_owner never sets this column explicitly, so
    # SQLAlchemy sends False on every fresh INSERT going forward).
    op.add_column(
        'users',
        sa.Column('email_verified', sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column('users', 'email_verified')
