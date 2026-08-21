"""merge anilupdated redis/kafka/risk chain with venky billing chain

Revision ID: 9ddae52d4dd6
Revises: ccabdbc4c745, f4a8c1d90b3e
Create Date: 2026-08-18 09:07:53.440477

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9ddae52d4dd6'
down_revision: Union[str, None] = ('ccabdbc4c745', 'f4a8c1d90b3e')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
