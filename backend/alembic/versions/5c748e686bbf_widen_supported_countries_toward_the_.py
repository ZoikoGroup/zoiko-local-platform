"""widen supported countries toward the market registry doc dimensions

Revision ID: 5c748e686bbf
Revises: 91b990e44d15
Create Date: 2026-08-22 19:05:58.977867

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '5c748e686bbf'
down_revision: Union[str, None] = '91b990e44d15'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "supported_countries",
        sa.Column("customer_type_restrictions", postgresql.ARRAY(sa.String(length=20)), nullable=True),
    )
    op.add_column(
        "supported_countries", sa.Column("porting_supported", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column(
        "supported_countries", sa.Column("recording_consent_basis", sa.String(length=50), nullable=True)
    )
    op.add_column(
        "supported_countries", sa.Column("payments_enabled", sa.Boolean(), nullable=False, server_default="false")
    )
    op.add_column(
        "supported_countries",
        sa.Column("marketing_claims_approved", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("supported_countries", "marketing_claims_approved")
    op.drop_column("supported_countries", "payments_enabled")
    op.drop_column("supported_countries", "recording_consent_basis")
    op.drop_column("supported_countries", "porting_supported")
    op.drop_column("supported_countries", "customer_type_restrictions")
