"""merge venky and anil migration heads

Revision ID: ab687051413a
Revises: 65f0fff3c13f, 9f3ae057ccb3
Create Date: 2026-08-03 20:47:09.346359

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab687051413a'
down_revision: Union[str, None] = ('65f0fff3c13f', '9f3ae057ccb3')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
