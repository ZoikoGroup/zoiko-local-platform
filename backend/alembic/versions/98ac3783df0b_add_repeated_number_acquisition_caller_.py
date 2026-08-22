"""add repeated number acquisition caller id change and account takeover signals

Revision ID: 98ac3783df0b
Revises: 9a6d3bb94cce
Create Date: 2026-08-22 17:35:27.392460

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '98ac3783df0b'
down_revision: Union[str, None] = '9a6d3bb94cce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Same shape as e590db9f87e6/9c1f4a0d2e77's CONCURRENT_CALL_LIMIT_EXCEEDED/
    # DEVICE_FINGERPRINT_ABUSE additions - the Python enum values need to
    # exist in the live Postgres enum type too, or record_risk_signal
    # raises "invalid input value for enum risksignaltype" the first time
    # any of these three actually fire.
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'REPEATED_NUMBER_ACQUISITION'")
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'CALLER_ID_CHANGE_PATTERN'")
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'ACCOUNT_TAKEOVER_INDICATOR'")


def downgrade() -> None:
    # Enum VALUE removal isn't supported by Postgres - these three stay
    # defined even on downgrade, same tradeoff as the precedent migrations.
    pass
