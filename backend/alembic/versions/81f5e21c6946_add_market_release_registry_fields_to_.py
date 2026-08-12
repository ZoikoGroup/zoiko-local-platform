"""add market release registry fields to number eligibility rules

Revision ID: 81f5e21c6946
Revises: e0f8f78c88dd
Create Date: 2026-08-12 16:02:15.360868

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '81f5e21c6946'
down_revision: Union[str, None] = 'e0f8f78c88dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Note: autogenerate also picked up pre-existing, unrelated drift (an
# 'agent_presence' unique-constraint name and 'calling_rates'
# nullability/constraint-naming mismatch) - deliberately left out, same
# as prior migrations' notes.

CALLING_DIRECTION_ENUM = sa.Enum('inbound_only', 'outbound_only', 'both', name='calling_direction_enum')


def upgrade() -> None:
    CALLING_DIRECTION_ENUM.create(op.get_bind(), checkfirst=True)
    # server_default on each so this is safe even if rows already exist
    # (none do as of this migration - see NumberEligibilityRule's
    # docstring - but ADD COLUMN NOT NULL with no default fails on a
    # non-empty table regardless).
    op.add_column(
        'number_eligibility_rules',
        sa.Column('emergency_calling_supported', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'number_eligibility_rules',
        sa.Column('recording_supported', sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        'number_eligibility_rules',
        sa.Column('allowed_calling_directions', CALLING_DIRECTION_ENUM, nullable=False, server_default='both'),
    )


def downgrade() -> None:
    op.drop_column('number_eligibility_rules', 'allowed_calling_directions')
    op.drop_column('number_eligibility_rules', 'recording_supported')
    op.drop_column('number_eligibility_rules', 'emergency_calling_supported')
    CALLING_DIRECTION_ENUM.drop(op.get_bind(), checkfirst=True)
