"""add emergency_calling_supported to supported_countries

Revision ID: f606e9a82525
Revises: fea5fe50cf35
Create Date: 2026-08-12 13:48:42.320819

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f606e9a82525'
down_revision: Union[str, None] = 'fea5fe50cf35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §10/§34 - defaults False
    # for every existing row: no market here has verified E911 evidence
    # today, so the disclosure-accuracy flag must start conservative, not
    # silently claim capability for already-seeded countries.
    op.add_column(
        'supported_countries',
        sa.Column('emergency_calling_supported', sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column('supported_countries', 'emergency_calling_supported')
