"""cap free trial at one number per the pricing doc

Revision ID: ccabdbc4c745
Revises: ba1a2c3c220b
Create Date: 2026-08-17 15:29:13.018405

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ccabdbc4c745'
down_revision: Union[str, None] = 'ba1a2c3c220b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Global Plans, Pricing & Commercial Launch Standard doc §7 "Trial
# Architecture": "Number entitlement: Maximum one trial number in eligible
# markets." free_trial was seeded at 3 (a leftover pre-doc default) - this
# doesn't touch any paid plan's max_numbers, only the trial one this
# specific doc line governs.
_OLD_VALUE = 3
_NEW_VALUE = 1


def upgrade() -> None:
    op.execute(f"UPDATE plans SET max_numbers = {_NEW_VALUE} WHERE plan_code = 'free_trial'")


def downgrade() -> None:
    op.execute(f"UPDATE plans SET max_numbers = {_OLD_VALUE} WHERE plan_code = 'free_trial'")
