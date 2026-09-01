"""merge venky into anilupdated: scheduled plan changes, risk signals, staff account management grant

Revision ID: 8b345518d881
Revises: c2580a9fed09, a80b7b11ce8e, a4f7c2e8b19d
Create Date: 2026-08-27 17:16:42.909573

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b345518d881'
down_revision: Union[str, None] = ('c2580a9fed09', 'a80b7b11ce8e', 'a4f7c2e8b19d')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
