"""Bug ZL-11 fix: Starter plan's max_team_seats didn't match its team.enabled entitlement

Revision ID: 7411ac7ac55c
Revises: e5b3f9a1c7d4
Create Date: 2026-09-25

Migration 245b5ab62c15 deliberately restricted the `team.enabled` entitlement
to {business, pro, scale} - its own comment says "the doc's matrix says
Starter should have NO team-member capability." But it only touched the
plan_entitlements table, never the original cdc47723ab0f seed's
`max_team_seats: 5` on the `starter` plan row itself - so a Starter customer
still sees "seats: 0 of 5" on their Billing & Usage page (a real allocation
number), while the Business page (the only place seats can actually be
managed) is locked behind an entitlement Starter was never granted. Real
tester-reported bug: "Seat Management Functionality Missing Despite Seat
Allocation in Subscription Plans."

Sets starter.max_team_seats to 1 (just the account owner - no room to add
anyone else), matching the entitlement decision that was already made but
never reflected here. free_trial is deliberately left untouched - showing a
locked preview of a paid-tier feature during a trial is a distinct, arguably
intentional product decision, not the same stale-data mismatch this fixes.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "7411ac7ac55c"
down_revision: Union[str, None] = "e5b3f9a1c7d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

plans = sa.table(
    "plans",
    sa.column("plan_code", sa.String),
    sa.column("max_team_seats", sa.Integer),
)


def upgrade() -> None:
    op.execute(plans.update().where(plans.c.plan_code == "starter").values(max_team_seats=1))


def downgrade() -> None:
    op.execute(plans.update().where(plans.c.plan_code == "starter").values(max_team_seats=5))
