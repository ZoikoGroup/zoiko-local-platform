"""add real oauth token storage to crm connections

Revision ID: 8073c2599e48
Revises: 8a1c4e7f9b3d
Create Date: 2026-08-06 20:19:15.498275

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8073c2599e48'
down_revision: Union[str, None] = '8a1c4e7f9b3d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("crm_connections", sa.Column("access_token_encrypted", sa.String(length=2000), nullable=True))
    op.add_column("crm_connections", sa.Column("refresh_token_encrypted", sa.String(length=2000), nullable=True))
    op.add_column("crm_connections", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("crm_connections", "token_expires_at")
    op.drop_column("crm_connections", "refresh_token_encrypted")
    op.drop_column("crm_connections", "access_token_encrypted")
