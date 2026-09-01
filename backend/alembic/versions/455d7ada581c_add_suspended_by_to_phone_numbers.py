"""add suspended_by to phone_numbers

Revision ID: 455d7ada581c
Revises: 9acc9a1a81de
Create Date: 2026-09-01 10:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '455d7ada581c'
down_revision: Union[str, None] = '9acc9a1a81de'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Distinguishes a customer-chosen suspension (suspend_number) from a
    # risk-engine-driven one (suspend_numbers_for_account_by_system) - both
    # previously left the number in plain status == SUSPENDED with no way
    # to tell them apart, so staff reinstating a risk-suspended account
    # (reactivate_numbers_for_account_by_staff) was also silently
    # reactivating numbers a customer had chosen to suspend themselves.
    # NULL for any number suspended before this column existed, or
    # whenever status isn't SUSPENDED - the staff-reactivation query
    # treats NULL the same as "not system-suspended" and leaves it alone,
    # the fail-safe posture.
    # This codebase's Enum(SomeStrEnum) columns store the Python member's
    # .name (uppercase), not .value, with no values_callable override
    # anywhere (see RiskSignal.signal_type's docstring for the same point) -
    # match that here: 'CUSTOMER'/'SYSTEM', not 'customer'/'system'.
    suspension_source_enum = sa.Enum('CUSTOMER', 'SYSTEM', name='phone_number_suspension_source_enum')
    suspension_source_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        'phone_numbers',
        sa.Column('suspended_by', suspension_source_enum, nullable=True),
    )


def downgrade() -> None:
    op.drop_column('phone_numbers', 'suspended_by')
    sa.Enum(name='phone_number_suspension_source_enum').drop(op.get_bind(), checkfirst=True)
