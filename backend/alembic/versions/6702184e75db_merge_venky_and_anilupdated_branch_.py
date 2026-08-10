"""merge venky and anilupdated branch migration heads

Revision ID: 6702184e75db
Revises: 46b8d21987b5, 69c217951fde
Create Date: 2026-08-08 11:37:14.837112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6702184e75db'
down_revision: Union[str, None] = ('46b8d21987b5', '69c217951fde')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
