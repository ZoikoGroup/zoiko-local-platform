"""merge venky twilio regulatory bundle and video recording chain with anilupdated country widening chain

Revision ID: 71707ef5dc0a
Revises: 173a9b21818e, 5c748e686bbf
Create Date: 2026-08-24 10:19:50.186477

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '71707ef5dc0a'
down_revision: Union[str, None] = ('173a9b21818e', '5c748e686bbf')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
