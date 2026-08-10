"""add read_at to notification deliveries

Revision ID: a3f5c9d2e148
Revises: e1359a68dde8
Create Date: 2026-08-04 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a3f5c9d2e148'
down_revision: Union[str, None] = 'e1359a68dde8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: this chain's read_at column is the same one 141799fdba9b
    # already added on the parallel branch, merged in by a later revision -
    # applying both would try to add the column twice. Mirrors the identical
    # fix already made on 141799fdba9b's side of this same duplicate pair.
    pass


def downgrade() -> None:
    pass
