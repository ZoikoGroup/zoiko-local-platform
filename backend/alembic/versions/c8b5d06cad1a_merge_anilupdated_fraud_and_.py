"""merge anilupdated fraud/notification-suppression branch head with venky head

Revision ID: c8b5d06cad1a
Revises: 8e3f1a5d92c7, e7b2c9a1f5d6
Create Date: 2026-08-10 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8b5d06cad1a'
down_revision: Union[str, None] = ('8e3f1a5d92c7', 'e7b2c9a1f5d6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
