"""merge venky into anilupdated: number_release kill switch, email verification, KYC re-verification

Revision ID: 2faee6bbefe0
Revises: 9f1c6d4a2b83, e5bf995f9481
Create Date: 2026-08-31 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2faee6bbefe0'
down_revision: Union[str, None] = ('9f1c6d4a2b83', 'e5bf995f9481')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
