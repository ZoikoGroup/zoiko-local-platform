"""add invoice_generated and payment_collected zoikonex sync event types

Revision ID: 7852c57ad1b9
Revises: 0fd38b72ef14
Create Date: 2026-08-11 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '7852c57ad1b9'
down_revision: Union[str, None] = '0fd38b72ef14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'invoice_generated'")
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'payment_collected'")


def downgrade() -> None:
    # No DROP VALUE in Postgres - permanent no-op, same as every other
    # enum-extending migration in this codebase.
    pass
