"""merge venky and origin notification-channel migration heads

Revision ID: ead458d5e6a7
Revises: 141799fdba9b, c8e14b6a2f3d
Create Date: 2026-08-05 10:01:01.416603

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ead458d5e6a7'
down_revision: Union[str, None] = ('141799fdba9b', 'c8e14b6a2f3d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
