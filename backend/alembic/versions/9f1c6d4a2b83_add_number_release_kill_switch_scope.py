"""add NUMBER_RELEASE value to kill_switch_scope_enum

Revision ID: 9f1c6d4a2b83
Revises: c2580a9fed09
Create Date: 2026-08-31 00:00:00.000000

Commercial Billing Operating Standard doc §32.1 lists "number release" as
its own named kill-switch scope, distinct from "new number provisioning" -
KillSwitchScope (app.ops.models) only had NUMBER_PROVISIONING/
OUTBOUND_CALLING/AI_PROCESSING/PAYMENTS_BILLING before this, so an incident
that should freeze number cancellations had no scope to freeze. Schema
change only - no data written here, no existing rows reference this value.

Same autocommit_block() requirement as 2aca0e0b665f (Postgres refuses to
use a freshly added enum value inside the transaction that added it, and
alembic/env.py wraps every pending revision from one `alembic upgrade
head` run in a single shared transaction) - applied here even though this
migration doesn't itself use the new value, since a later migration or the
app code deployed in the same release could.

Label is 'NUMBER_RELEASE' (matching the Python enum MEMBER NAME), not
'number_release' - this table's Enum(KillSwitchScope, ...) column was
declared without values_callable, so SQLAlchemy's default Enum(str, ...)
handling persists .name, not .value, as the actual Postgres label (every
existing label in this type - NUMBER_PROVISIONING, OUTBOUND_CALLING, etc.
- is already uppercase for the same reason).
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '9f1c6d4a2b83'
down_revision: Union[str, None] = 'c2580a9fed09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE kill_switch_scope_enum ADD VALUE IF NOT EXISTS 'NUMBER_RELEASE'")


def downgrade() -> None:
    # Enum VALUE removal isn't supported by Postgres - same documented
    # tradeoff as 2aca0e0b665f/2cb8cec0ce38, value stays defined even on
    # downgrade.
    pass
