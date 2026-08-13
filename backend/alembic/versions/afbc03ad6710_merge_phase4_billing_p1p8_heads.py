"""merge phase4/billing-p1p8 heads

Revision ID: afbc03ad6710
Revises: 1c78de8b7051, 6bda3e0f8c15
Create Date: 2026-08-13 10:30:09.325800

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'afbc03ad6710'
down_revision: Union[str, None] = ('1c78de8b7051', '6bda3e0f8c15')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
