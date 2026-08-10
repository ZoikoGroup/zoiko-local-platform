"""add sms_enabled to phone_numbers (SMS by regulated market, Phase 3)

Revision ID: 69c217951fde
Revises: f30bad6aa460
Create Date: 2026-08-07 09:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '69c217951fde'
down_revision: Union[str, None] = 'f30bad6aa460'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('phone_numbers', sa.Column('sms_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('phone_numbers', 'sms_enabled', server_default=None)


def downgrade() -> None:
    op.drop_column('phone_numbers', 'sms_enabled')
