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
    # app/risk/models.py's FraudRule and FraudCase classes (and the
    # GEOGRAPHIC_DISPERSION enum value) came in with the origin/dev merge
    # with no migration ever written for them - they only existed in this
    # dev DB because the test suite's Base.metadata.create_all() silently
    # created them alongside every other model. A fresh DB running only
    # `alembic upgrade head` (CLAUDE.md's documented setup path) would be
    # missing all three. This migration is that missing piece, plus the
    # new SPEND_LIMIT_EXCEEDED value this change adds on top.
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'SPEND_LIMIT_EXCEEDED'")

    op.create_table(
        'fraud_rules',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            'signal_type',
            postgresql.ENUM(
                'VELOCITY_EXCEEDED', 'BLOCKED_DESTINATION_ATTEMPT', 'GEOGRAPHIC_DISPERSION', 'SPEND_LIMIT_EXCEEDED',
                name='risksignaltype', create_type=False,
            ),
            nullable=False,
        ),
        sa.Column('weight', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('signal_type'),
    )

    fraud_case_status_enum = postgresql.ENUM('OPEN', 'CONFIRMED', 'CLEARED', name='fraudcasestatus')
    fraud_case_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'fraud_cases',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('score_at_open', sa.Integer(), nullable=False),
        sa.Column('status', postgresql.ENUM('OPEN', 'CONFIRMED', 'CLEARED', name='fraudcasestatus', create_type=False), nullable=False),
        sa.Column('resolved_by', sa.String(length=255), nullable=True),
        sa.Column('resolution_notes', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_fraud_cases_account_id'), 'fraud_cases', ['account_id'], unique=False)
    op.create_index(op.f('ix_fraud_cases_created_at'), 'fraud_cases', ['created_at'], unique=False)

    # Seed weights matching the values service.py already hardcoded for the
    # two pre-existing signal types, plus a first-pass weight for
    # geographic dispersion (previously unscored). SPEND_LIMIT_EXCEEDED is
    # deliberately NOT seeded here - it falls back to service.py's
    # _DEFAULT_WEIGHTS (same "no active row -> conservative built-in
    # default" design FraudRule's docstring already describes), which also
    # sidesteps Postgres's rule against using a freshly ALTER TYPE ADD
    # VALUE'd label inside the same transaction that added it.
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
            {"id": str(uuid.uuid4()), "signal_type": "VELOCITY_EXCEEDED", "weight": 30, "is_active": True},
            {"id": str(uuid.uuid4()), "signal_type": "BLOCKED_DESTINATION_ATTEMPT", "weight": 40, "is_active": True},
            {"id": str(uuid.uuid4()), "signal_type": "GEOGRAPHIC_DISPERSION", "weight": 50, "is_active": True},
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_fraud_cases_created_at'), table_name='fraud_cases')
    op.drop_index(op.f('ix_fraud_cases_account_id'), table_name='fraud_cases')
    op.drop_table('fraud_cases')
    sa.Enum(name='fraudcasestatus').drop(op.get_bind(), checkfirst=True)
    op.drop_table('fraud_rules')
    # Removing an enum VALUE (as opposed to the whole type) isn't supported
    # by Postgres - SPEND_LIMIT_EXCEEDED stays defined even on downgrade,
    # same tradeoff the original risksignaltype migration already accepted.
