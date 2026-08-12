"""add fraud_rules, fraud_cases tables and spend_limit_exceeded signal

Revision ID: 7a2e5c918bf4
Revises: ce2bebedfe43
Create Date: 2026-08-10 13:10:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '7a2e5c918bf4'
down_revision: Union[str, None] = 'ce2bebedfe43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CORRECTION (found running this chain against a genuinely fresh DB,
    # e.g. CI's ephemeral Postgres or a new Render/Neon instance - it
    # never occurs on this project's existing dev DB, where these tables
    # already exist): the NOTE below was wrong. 0d31d1ab1f2d is a no-op
    # (its own comment says fraud_rules/fraud_cases/fraudcasestatus are
    # created "by 7a2e5c918bf4" - i.e. here) and this migration never
    # actually created them either - both sides of that merge deferred to
    # the other, so on a genuinely empty database NEITHER table exists and
    # the bulk_insert below fails with UndefinedTable. Guarded with
    # has_table() (not a plain create_table) because this branch and
    # 0d31d1ab1f2d's branch share a common ancestor and Alembic does not
    # guarantee which one a future chain edit runs first - this must be
    # correct regardless of ordering, and a no-op on every environment
    # where the tables already exist (this project's real dev DB).
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('fraud_cases'):
        # Real label casing confirmed against the live dev DB: uppercase
        # .name-style ('OPEN'/'CONFIRMED'/'CLEARED'), matching this
        # codebase's usual Enum(PythonEnum) default - unlike
        # risksignaltype below, which is a documented exception.
        fraud_case_status = postgresql.ENUM('OPEN', 'CONFIRMED', 'CLEARED', name='fraudcasestatus')
        fraud_case_status.create(bind, checkfirst=True)
        op.create_table(
            'fraud_cases',
            sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column('account_id', postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column('score_at_open', sa.Integer(), nullable=False),
            sa.Column(
                'status',
                postgresql.ENUM('OPEN', 'CONFIRMED', 'CLEARED', name='fraudcasestatus', create_type=False),
                nullable=False,
            ),
            sa.Column('resolved_by', sa.String(length=255), nullable=True),
            sa.Column('resolution_notes', sa.String(length=500), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index(op.f('ix_fraud_cases_account_id'), 'fraud_cases', ['account_id'], unique=False)
        op.create_index(op.f('ix_fraud_cases_created_at'), 'fraud_cases', ['created_at'], unique=False)

    if not inspector.has_table('fraud_rules'):
        # risksignaltype already exists by this point (created in
        # a80b7b11ce8e_add_risk_signals_table.py, an ancestor of this
        # revision on every path that reaches it) - reused via
        # create_type=False, never recreated.
        op.create_table(
            'fraud_rules',
            sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column('signal_type', postgresql.ENUM(name='risksignaltype', create_type=False), nullable=False),
            sa.Column('weight', sa.Integer(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('signal_type'),
        )

    # risksignaltype's existing labels are lowercase .value-style (a
    # documented exception to this codebase's usual uppercase-.name
    # convention for enums - see 0d31d1ab1f2d's note on adding
    # 'geographic_dispersion') - must match, not the uppercase this
    # migration originally used.
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'spend_limit_exceeded'")

    # Seed weights matching the values service.py already hardcodes for
    # these three pre-existing signal types (see risk/service.py's
    # _DEFAULT_WEIGHTS) - a real, staff-editable FraudRule row is better
    # than only ever relying on the code fallback. spend_limit_exceeded is
    # deliberately NOT seeded here - it falls back to _DEFAULT_WEIGHTS
    # (same "no active row -> conservative built-in default" design
    # FraudRule's docstring already describes), which also sidesteps
    # Postgres's rule against using a freshly ALTER TYPE ADD VALUE'd label
    # inside the same transaction that added it.
    fraud_rules_table = sa.table(
        'fraud_rules',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('signal_type', sa.String),
        sa.column('weight', sa.Integer),
        sa.column('is_active', sa.Boolean),
    )
    op.bulk_insert(
        fraud_rules_table,
        [
            {"id": str(uuid.uuid4()), "signal_type": "velocity_exceeded", "weight": 30, "is_active": True},
            {"id": str(uuid.uuid4()), "signal_type": "blocked_destination_attempt", "weight": 40, "is_active": True},
            {"id": str(uuid.uuid4()), "signal_type": "geographic_dispersion", "weight": 25, "is_active": True},
        ],
    )


def downgrade() -> None:
    # Reverses the has_table()-guarded create_table calls in upgrade() -
    # drops the tables outright rather than only the 3 seeded rows, since
    # this migration now owns their creation (see upgrade()'s docstring).
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table('fraud_cases'):
        op.drop_index(op.f('ix_fraud_cases_created_at'), table_name='fraud_cases')
        op.drop_index(op.f('ix_fraud_cases_account_id'), table_name='fraud_cases')
        op.drop_table('fraud_cases')
        postgresql.ENUM(name='fraudcasestatus').drop(bind, checkfirst=True)

    if inspector.has_table('fraud_rules'):
        op.drop_table('fraud_rules')
        # risksignaltype itself is NOT dropped - it's owned by
        # a80b7b11ce8e_add_risk_signals_table.py, not this migration.

    # Removing an enum VALUE (as opposed to the whole type) isn't supported
    # by Postgres - SPEND_LIMIT_EXCEEDED stays defined even on downgrade,
    # same tradeoff the original risksignaltype migration already accepted.
