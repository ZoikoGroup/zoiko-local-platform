"""add payment-not-captured reconciliation exception type

Revision ID: d1f7a3e9c052
Revises: c4a891fe6d27
Create Date: 2026-08-20 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd1f7a3e9c052'
down_revision: Union[str, None] = 'c4a891fe6d27'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Enum stores Python enum MEMBER NAMES, not .value strings - same
    # confirmed-live convention as 7d61853cb8ac's catalog_entry_status_enum
    # note.
    op.execute(
        "ALTER TYPE zoikonex_reconciliation_exception_type_enum "
        "ADD VALUE 'PAYMENT_AUTHORISED_NOT_CAPTURED'"
    )
    op.add_column(
        'zoikonex_reconciliation_runs',
        sa.Column('uncaptured_payments_found', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('zoikonex_reconciliation_runs', 'uncaptured_payments_found')
    # Postgres has no ALTER TYPE ... DROP VALUE - same limitation noted by
    # every other enum-extending migration in this codebase.
