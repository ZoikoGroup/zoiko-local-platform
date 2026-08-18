"""add concurrent_call_limit_exceeded signal

Revision ID: e590db9f87e6
Revises: 6c342a8aba37
Create Date: 2026-08-18 00:00:04.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e590db9f87e6'
down_revision: Union[str, None] = '6c342a8aba37'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # RiskSignalType.CONCURRENT_CALL_LIMIT_EXCEEDED (app/risk/models.py) was
    # added to the Python enum but never to the live Postgres enum type -
    # every real call to assert_concurrent_call_limit_ok's record_risk_signal
    # raised "invalid input value for enum risksignaltype" (a DBAPIError),
    # which app.main's generic database_unavailable_handler turns into a
    # 503 - masking the intended 429 ConcurrentCallLimitExceededError
    # response behind a misleading "service unavailable" error. Same
    # uppercase .name-style casing as every other value on this enum (see
    # 9c1f4a0d2e77's DEVICE_FINGERPRINT_ABUSE for the identical fix shape).
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'CONCURRENT_CALL_LIMIT_EXCEEDED'")


def downgrade() -> None:
    # Enum VALUE removal isn't supported by Postgres - CONCURRENT_CALL_LIMIT_EXCEEDED
    # stays defined even on downgrade, same tradeoff as DEVICE_FINGERPRINT_ABUSE.
    pass
