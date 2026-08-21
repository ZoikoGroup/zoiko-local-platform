"""reconcile schema drift found after merging anilupdated: risk_state enum rename, rate meter version, rate created_at not null

Revision ID: 0b6951aec754
Revises: 9ddae52d4dd6
Create Date: 2026-08-18 09:17:26.056652

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0b6951aec754'
down_revision: Union[str, None] = '9ddae52d4dd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# `alembic check` against this dev DB (shared across concurrent sessions -
# see CLAUDE.md's standing note on expecting exactly this) found three real
# drifts once the anilupdated and venky chains were merged and replayed
# here:
#
# 1. accounts.risk_state's actual backing type is `risk_state_enum` - this
#    DB already had the column from a different history path before
#    b2e6c4a19f03 (this merge's "add risk_state to accounts" migration)
#    reached it under its own `account_risk_state_enum` type name, and
#    that migration's defensive checkfirst on the column-add (added in
#    this same merge, see that file) correctly skipped re-adding the
#    column but left the type name mismatched against the model. Same
#    values either way - a type rename, not a data change.
# 2. number_rates.created_at / ai_usage_rates.created_at were created
#    NOT NULL in the ORM models (61bc6e50e6db) but that migration's raw
#    op.create_table calls never set nullable=False explicitly, so the
#    live columns came out nullable. No non-null default value change
#    needed, both columns are always populated by server_default=now().
# 3. usage_events.rate_meter_version - df9eadb7ef87 is stamped as applied
#    in this DB's alembic_version history, but the column it adds was
#    never actually present (confirmed via a live information_schema
#    query) - re-added here defensively rather than trying to re-run a
#    migration alembic already considers done.


def upgrade() -> None:
    # No server_default expected here - Account.risk_state's default is
    # Python-side only (default=AccountRiskState.TRIAL_LOW), matching how
    # b2e6c4a19f03 explicitly clears its own temporary backfill default
    # (op.alter_column(..., server_default=None)) once the backfill runs.
    op.execute("ALTER TABLE accounts ALTER COLUMN risk_state DROP DEFAULT")
    op.execute("ALTER TABLE accounts ALTER COLUMN risk_state TYPE account_risk_state_enum USING risk_state::text::account_risk_state_enum")
    op.execute("DROP TYPE IF EXISTS risk_state_enum")

    op.alter_column('number_rates', 'created_at', nullable=False)
    op.alter_column('ai_usage_rates', 'created_at', nullable=False)

    inspector = sa.inspect(op.get_bind())
    existing_columns = {col['name'] for col in inspector.get_columns('usage_events')}
    if 'rate_meter_version' not in existing_columns:
        op.add_column('usage_events', sa.Column('rate_meter_version', sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column('usage_events', 'rate_meter_version')
    op.alter_column('ai_usage_rates', 'created_at', nullable=True)
    op.alter_column('number_rates', 'created_at', nullable=True)

    account_risk_state_enum_old = sa.Enum(
        'TRIAL_LOW', 'TRIAL_VERIFIED', 'PAID_NORMAL', 'REVIEW_REQUIRED', 'SUSPENDED_FRAUD',
        name='risk_state_enum',
    )
    account_risk_state_enum_old.create(op.get_bind(), checkfirst=True)
    op.execute("ALTER TABLE accounts ALTER COLUMN risk_state TYPE risk_state_enum USING risk_state::text::risk_state_enum")
