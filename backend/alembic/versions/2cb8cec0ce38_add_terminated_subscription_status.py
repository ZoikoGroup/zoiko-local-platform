"""add TERMINATED subscription status and terminate_subscription billing action

Revision ID: 2cb8cec0ce38
Revises: 1a42d011d8c7
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '2cb8cec0ce38'
down_revision: Union[str, None] = '1a42d011d8c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §M3 - a real terminal state
    # distinct from CANCELED (see SubscriptionStatus.TERMINATED's docstring).
    op.execute("ALTER TYPE subscription_status_enum ADD VALUE IF NOT EXISTS 'TERMINATED'")
    op.execute("ALTER TYPE billing_action_type_enum ADD VALUE IF NOT EXISTS 'TERMINATE_SUBSCRIPTION'")
    op.add_column('subscriptions', sa.Column('terminated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('subscriptions', 'terminated_at')
    # Enum VALUE removal isn't supported by Postgres - TERMINATED/
    # TERMINATE_SUBSCRIPTION stay defined even on downgrade, same tradeoff
    # documented in e590db9f87e6 and 9c1f4a0d2e77.
