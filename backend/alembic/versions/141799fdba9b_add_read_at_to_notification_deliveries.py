"""add read_at to notification deliveries

Revision ID: 141799fdba9b
Revises: 323f90b0da5d
Create Date: 2026-08-04 17:37:11.270767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '141799fdba9b'
down_revision: Union[str, None] = '323f90b0da5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: this chain's read_at column is the same one a3f5c9d2e148 already
    # added on the parallel (venky) branch, merged in by a later revision -
    # applying both would try to add the column twice.
    pass


def downgrade() -> None:
    pass
