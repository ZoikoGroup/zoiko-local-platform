"""add missing concurrent_call_limit_exceeded value to risksignaltype enum

Revision ID: 0f20fbb33954
Revises: 0b6951aec754
Create Date: 2026-08-18 10:17:30.033664

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0f20fbb33954'
down_revision: Union[str, None] = '0b6951aec754'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Found live while chasing a 503 on the concurrent-call-limit tests after
# merging: app.risk.models.RiskSignalType.CONCURRENT_CALL_LIMIT_EXCEEDED
# was added to the Python enum after 351fca0d8b24 (the migration that
# uppercased this type's other 5 labels) was already written, so it never
# got its own ADD VALUE - assert_concurrent_call_limit_ok's own
# record_risk_signal call was failing with psycopg2.errors.
# InvalidTextRepresentation on every real trip of this control, which the
# top-level DBAPIError handler was masking as a generic 503 instead of the
# actual cause.
#
# IF NOT EXISTS (PG 12+) - same "correct regardless of which state it
# finds" posture as 351fca0d8b24's own existence guards.


def upgrade() -> None:
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'CONCURRENT_CALL_LIMIT_EXCEEDED'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enum types - removing a label
    # requires rebuilding the type from scratch (create new type, cast
    # every dependent column, drop old type), which isn't worth doing for
    # a downgrade path. Leaving the value in place on downgrade is safe:
    # it's additive, and nothing reads "is this enum type missing an
    # unrelated future value" as a correctness signal.
    pass
