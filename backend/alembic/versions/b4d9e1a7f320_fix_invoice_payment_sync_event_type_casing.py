"""fix casing of invoice/payment zoikonex sync event types

Revision ID: b4d9e1a7f320
Revises: a1c3d8f2e5b7
Create Date: 2026-08-11 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b4d9e1a7f320'
down_revision: Union[str, None] = 'a1c3d8f2e5b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 7852c57ad1b9 added 'invoice_generated'/'payment_collected' (lowercase,
# matching ZoikoNexSyncEventType's .value) - wrong. SQLAlchemy's Enum
# column here serializes by member .name, not .value (confirmed live: the
# three pre-existing labels in this same Postgres enum type are
# 'SUBSCRIPTION_SYNC'/'USAGE_SYNC'/'PAYMENT_EVENT_RECEIVED', all uppercase
# names, not their lowercase_snake .value strings) - every INSERT of
# INVOICE_GENERATED/PAYMENT_COLLECTED failed with "invalid input value for
# enum" until this. The two lowercase labels from 7852c57ad1b9 are now
# permanent, unused cruft - Postgres has no DROP VALUE, same as every
# other enum-extending migration in this codebase.
def upgrade() -> None:
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'INVOICE_GENERATED'")
    op.execute("ALTER TYPE zoikonex_sync_event_type_enum ADD VALUE IF NOT EXISTS 'PAYMENT_COLLECTED'")


def downgrade() -> None:
    pass
