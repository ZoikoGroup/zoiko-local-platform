"""merge anilupdated into venky: staff console redesign + kill switches UI

Revision ID: f1a9c72e6d40
Revises: 0251c73f5e21, b3e7a1d9c624
Create Date: 2026-09-01 00:00:00.000000

Real, concurrent duplicate-feature collision (not just a migration-graph
diamond): venky's b3e7a1d9c624 seeded staff.manage_staff for a
/staff/members staff-management screen; anilupdated independently built
the same feature (POST/PUT /staff/team/*, staff.manage_staff_accounts,
seeded by a4f7c2e8b19d) as part of a much larger staff console redesign
(RBAC-differentiated nav, theme toggle, platform metrics). Resolved in
favor of anilupdated's version - the venky-side routes/schemas/service
functions and frontend /staff/members plumbing were removed in the merge
commit itself. staff.manage_staff (from b3e7a1d9c624) is no longer
checked by any route as of this merge; see the next migration for its
cleanup.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a9c72e6d40'
down_revision: Union[str, None] = ('0251c73f5e21', 'b3e7a1d9c624')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
