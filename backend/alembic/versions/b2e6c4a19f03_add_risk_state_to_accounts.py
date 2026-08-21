"""add trial-abuse step-up risk_state to accounts

Revision ID: b2e6c4a19f03
Revises: 7f3c9a1e4d82
Create Date: 2026-08-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b2e6c4a19f03'
down_revision: Union[str, None] = '7f3c9a1e4d82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Production Readiness Standard doc's "trial-abuse step-up model" -
# TRIAL_LOW/TRIAL_VERIFIED/PAID_NORMAL/REVIEW_REQUIRED/SUSPENDED_FRAUD, a
# graduated trust tier per account that the concurrent-call limit
# (app.risk.service.MAX_CONCURRENT_CALLS_BY_RISK_STATE) and the fraud engine's
# automatic REVIEW_REQUIRED/SUSPENDED_FRAUD transitions key off of.
#
# Backfills every EXISTING account to PAID_NORMAL - an honest "preserve
# current de facto behavior" carry-forward (every account today already has
# unrestricted concurrent calling), same rationale as the market_status
# migration's PAID_OPEN backfill. Any account created after this migration
# defaults to TRIAL_LOW (fail-closed) via the model column default - this
# backfill is a one-time exception for accounts that already existed before
# this control existed.
#
# Also backfills any account with an OPEN fraud_cases row to REVIEW_REQUIRED
# - the one piece of "should this account actually be at a tighter tier
# already" that's cheaply and definitely knowable from existing data.
# Accounts the risk engine had already suspended before this column existed
# are NOT backfilled to SUSPENDED_FRAUD - there's no account-level "this was
# suspended for risk" flag anywhere today (only individual phone_numbers get
# suspended), so there's no reliable query for it; a real gap, but not one
# this migration can close without guessing.


def upgrade() -> None:
    account_risk_state_enum = postgresql.ENUM(
        'TRIAL_LOW', 'TRIAL_VERIFIED', 'PAID_NORMAL', 'REVIEW_REQUIRED', 'SUSPENDED_FRAUD',
        name='account_risk_state_enum',
    )
    account_risk_state_enum.create(op.get_bind(), checkfirst=True)

    # checkfirst - a shared dev DB can already have this column from a
    # concurrent branch's history reaching the same DDL under a different
    # revision id before the chains were merged (same class of bug already
    # fixed elsewhere in this chain - see "Fix migration chain bugs found
    # by replaying it fresh"). Column-add has no checkfirst kwarg of its
    # own, unlike the enum-type create above, so guard it explicitly.
    inspector = sa.inspect(op.get_bind())
    existing_columns = {col['name'] for col in inspector.get_columns('accounts')}
    if 'risk_state' not in existing_columns:
        op.add_column(
            'accounts',
            sa.Column(
                'risk_state',
                postgresql.ENUM(name='account_risk_state_enum', create_type=False),
                nullable=False,
                server_default='TRIAL_LOW',
            ),
        )
        op.execute("UPDATE accounts SET risk_state = 'PAID_NORMAL'")
        op.execute(
            """
            UPDATE accounts SET risk_state = 'REVIEW_REQUIRED'
            WHERE id IN (SELECT account_id FROM fraud_cases WHERE status = 'OPEN')
            """
        )
        op.alter_column('accounts', 'risk_state', server_default=None)


def downgrade() -> None:
    op.drop_column('accounts', 'risk_state')
    sa.Enum(name='account_risk_state_enum').drop(op.get_bind(), checkfirst=True)
