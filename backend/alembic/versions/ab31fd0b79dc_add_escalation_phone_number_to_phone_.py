"""add escalation_phone_number to phone_numbers, distinct from forwarding_number

Revision ID: ab31fd0b79dc
Revises: 01f92b32f213
Create Date: 2026-08-22 12:09:35.664958

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ab31fd0b79dc'
down_revision: Union[str, None] = '01f92b32f213'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('phone_numbers', sa.Column('escalation_phone_number', sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column('phone_numbers', 'escalation_phone_number')
