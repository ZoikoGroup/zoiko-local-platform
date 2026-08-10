"""add salesforce instance url to crm connections

Revision ID: de8172a21362
Revises: c1d9a047e3f2
Create Date: 2026-08-06 23:30:03.639704

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de8172a21362'
down_revision: Union[str, None] = 'c1d9a047e3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("crm_connections", sa.Column("instance_url", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("crm_connections", "instance_url")
