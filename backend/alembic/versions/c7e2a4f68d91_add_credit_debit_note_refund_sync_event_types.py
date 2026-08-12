"""add credit note, debit note, and refund zoikonex sync event types

Revision ID: c7e2a4f68d91
Revises: b4d9e1a7f320
Create Date: 2026-08-12 05:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c7e2a4f68d91'
down_revision: Union[str, None] = 'b4d9e1a7f320'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Uppercase names, not the enum members' lowercase_snake .value - SQLAlchemy's
# Enum column serializes by member .name (confirmed the hard way in
# b4d9e1a7f320, after 7852c57ad1b9 added the wrong casing).
def upgrade() -> None:
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'CREDIT_NOTE_ISSUED'")
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'DEBIT_NOTE_ISSUED'")
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'REFUND_ISSUED'")


def downgrade() -> None:
    # No DROP VALUE in Postgres - permanent no-op, same as every other
    # enum-extending migration in this codebase.
    pass
