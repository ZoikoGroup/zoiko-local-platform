"""grant super admin the account test-flag capability

Revision ID: db8d0f0b2e05
Revises: 27b720ab83e5
Create Date: 2026-08-22 11:32:02.813361

The new PUT /staff/accounts/{id}/test-flag route (app.staff.service.
set_account_test_flag) needs its own grant row, same SUPER_ADMIN-only bar
as staff.manage_billing_classification - is_test bypasses the
CONTROLLED_BETA/INTERNAL_TEST market-activation gate without a real legal
sign-off, so granting it is a platform-wide decision, not a routine
support action.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'db8d0f0b2e05'
down_revision: Union[str, None] = '27b720ab83e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    grants_table = sa.table(
        "staff_capability_grants",
        sa.column("id", sa.UUID(as_uuid=False)),
        sa.column("capability", sa.String),
        sa.column("role", sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "accounts.manage_test_flag", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'accounts.manage_test_flag'")
