"""remove orphaned staff.manage_staff grant (superseded by staff.manage_staff_accounts)

Revision ID: c8e3f56a1b09
Revises: f1a9c72e6d40
Create Date: 2026-09-01 00:00:01.000000

b3e7a1d9c624 seeded this grant for a /staff/members screen that the merge
in f1a9c72e6d40 removed in favor of anilupdated's equivalent
(/staff/team, staff.manage_staff_accounts, seeded by a4f7c2e8b19d - left
in place, still real and enforced). Leaving staff.manage_staff seeded
with no route checking it anymore would just be a dead, confusing entry
on the Access Matrix page. Not rewriting b3e7a1d9c624 itself - it may
already be applied elsewhere; this cleans up its effect with a new
migration instead, same as any other data correction.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'c8e3f56a1b09'
down_revision: Union[str, None] = 'f1a9c72e6d40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'staff.manage_staff'")


def downgrade() -> None:
    pass
