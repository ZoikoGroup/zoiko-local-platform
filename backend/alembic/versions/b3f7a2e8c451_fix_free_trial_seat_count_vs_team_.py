"""Bug ZL-11 fix (retest): free_trial plan's max_team_seats didn't match its team.enabled entitlement

Revision ID: b3f7a2e8c451
Revises: 9c2a7e4f1d63
Create Date: 2026-09-28 05:10:00.000000

Migration 7411ac7ac55c fixed this exact mismatch for the `starter` plan but
deliberately left `free_trial` untouched, on the reasoning that "showing a
locked preview of a paid-tier feature during a trial is a distinct,
arguably intentional product decision." Bug ZL-11 was retested and
re-reported against that same scenario: a Free Trial account's Billing &
Usage page still shows "seats: 0 of 5" (`Plan.max_team_seats`, seeded
cdc47723ab0f), but `team.enabled` is False for free_trial per the
ZL-COM-ENT-001 v3.0 entitlement register (fd300247929d, confirmed by
tests/test_team.py::test_free_trial_account_cannot_add_a_team_member) -
Business+ only, same restriction Starter has. The "intentional preview"
call didn't hold up against real tester feedback: it reads as a broken
promise, not a preview, since nothing in the product actually explains it
as an upgrade teaser.

Applies the identical fix 7411ac7ac55c already applied to starter: caps
free_trial.max_team_seats at 1 (just the account owner), so the numeric
allocation number stops advertising a capability the plan was never
entitled to. team.enabled itself is untouched - Business+ remains the
correct gate per the doc's own matrix, confirmed by the existing test
suite.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3f7a2e8c451"
down_revision: Union[str, None] = "9c2a7e4f1d63"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

plans = sa.table(
    "plans",
    sa.column("plan_code", sa.String),
    sa.column("max_team_seats", sa.Integer),
)


def upgrade() -> None:
    op.execute(plans.update().where(plans.c.plan_code == "free_trial").values(max_team_seats=1))


def downgrade() -> None:
    op.execute(plans.update().where(plans.c.plan_code == "free_trial").values(max_team_seats=5))
