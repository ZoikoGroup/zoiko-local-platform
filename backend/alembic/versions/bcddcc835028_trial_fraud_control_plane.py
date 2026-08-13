"""trial/fraud control plane: risk_state + account_kill_switches

Revision ID: bcddcc835028
Revises: e08eeaf76017
Create Date: 2026-08-13 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'bcddcc835028'
down_revision: Union[str, None] = 'e08eeaf76017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    risk_state_enum = sa.Enum(
        'TRIAL_LOW', 'TRIAL_VERIFIED', 'PAID_NORMAL', 'REVIEW_REQUIRED', 'SUSPENDED_FRAUD',
        name='risk_state_enum',
    )
    risk_state_enum.create(op.get_bind())

    op.add_column(
        'accounts', sa.Column('risk_state', risk_state_enum, nullable=False, server_default='TRIAL_LOW'),
    )

    # kill_switch_scope_enum already exists (c7b1743797ed's
    # platform_kill_switches migration) - reused here via create_type=False,
    # not recreated.
    kill_switch_scope_enum = postgresql.ENUM(
        'NUMBER_PROVISIONING', 'OUTBOUND_CALLING', 'AI_PROCESSING', 'PAYMENTS_BILLING',
        name='kill_switch_scope_enum', create_type=False,
    )
    op.create_table(
        'account_kill_switches',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('scope', kill_switch_scope_enum, nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('activated_by', sa.String(length=100), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id', 'scope', name='uq_account_kill_switch_account_scope'),
    )
    op.create_index(
        op.f('ix_account_kill_switches_account_id'), 'account_kill_switches', ['account_id'], unique=False,
    )

    # Every existing account is either already trial (TRIAL_LOW, the
    # column default) or already on a paid plan - promote the latter to
    # PAID_NORMAL now rather than waiting for the next risk-engine event
    # to notice, so the new field reflects reality immediately.
    op.execute("""
        UPDATE accounts SET risk_state = 'PAID_NORMAL'
        WHERE id IN (
            SELECT account_id FROM subscriptions WHERE plan_code != 'free_trial'
        )
    """)


def downgrade() -> None:
    op.drop_index(op.f('ix_account_kill_switches_account_id'), table_name='account_kill_switches')
    op.drop_table('account_kill_switches')
    op.drop_column('accounts', 'risk_state')
    op.execute("DROP TYPE IF EXISTS risk_state_enum")
