"""add is_test flag to accounts

Revision ID: 1c78de8b7051
Revises: c7b1743797ed
Create Date: 2026-08-12 17:18:20.094845

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c78de8b7051'
down_revision: Union[str, None] = 'c7b1743797ed'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §T stopgap - see
    # app.numbering.identity.models.Account.is_test's docstring for why
    # this is a narrow boolean, not the full billing_classification enum.
    # Defaults False for every existing account (none of them are test
    # accounts today).
    op.add_column('accounts', sa.Column('is_test', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column('accounts', 'is_test')
