"""add kyc_inquiry_id to compliance_cases

Revision ID: f12d9f227da0
Revises: de176a4fd695
Create Date: 2026-08-03 14:29:21.258881

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f12d9f227da0'
down_revision: Union[str, None] = 'de176a4fd695'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("compliance_cases", sa.Column("kyc_inquiry_id", sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column("compliance_cases", "kyc_inquiry_id")
