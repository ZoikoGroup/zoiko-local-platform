"""merge venky billing/events/compliance chain with anilupdated ai receptionist/risk chain

Revision ID: f6e5a8845023
Revises: a651ca32ef6b, fd2501d0b136
Create Date: 2026-08-21 10:36:43.283829

Two branches independently built the same "Pro/Scale get included AI
Receptionist minutes" feature: this branch added plans.
included_ai_receptionist_minutes (61bc6e50e6db) with no seed values, the
other branch (fd2501d0b136) seeds Pro=50/Scale=150 but has no ordering
guarantee against 61bc6e50e6db (siblings off a shared ancestor, not a
direct dependency) - seeding here instead, the first point in the DAG
where both branches are guaranteed to have already run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6e5a8845023'
down_revision: Union[str, None] = ('a651ca32ef6b', 'fd2501d0b136')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE plans SET included_ai_receptionist_minutes = 50 WHERE plan_code = 'pro'")
    op.execute("UPDATE plans SET included_ai_receptionist_minutes = 150 WHERE plan_code = 'scale'")


def downgrade() -> None:
    op.execute("UPDATE plans SET included_ai_receptionist_minutes = 0 WHERE plan_code IN ('pro', 'scale')")
