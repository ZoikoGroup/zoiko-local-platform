"""merge venky P0-8 wholesale reconciliation with dev P0 8-15

Revision ID: 4e50ef88c70b
Revises: 1c78de8b7051, a4cc9cb0ae0a
Create Date: 2026-08-13 12:45:29.840512

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4e50ef88c70b'
down_revision: Union[str, None] = ('1c78de8b7051', 'a4cc9cb0ae0a')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
