"""add call_queues, queue_members, agent_presence, queue_call_logs (contact-center-lite, Phase 3)

Revision ID: 8598eaf03f33
Revises: 4b2d27efe669
Create Date: 2026-08-06 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8598eaf03f33'
down_revision: Union[str, None] = '4b2d27efe669'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'call_queues',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('max_wait_seconds', sa.Integer(), nullable=False),
        sa.Column('wrap_up_seconds', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_call_queues_account_id'), 'call_queues', ['account_id'], unique=False)

    op.create_table(
        'queue_members',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('queue_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['queue_id'], ['call_queues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('queue_id', 'user_id', name='uq_queue_member'),
    )
    op.create_index(op.f('ix_queue_members_queue_id'), 'queue_members', ['queue_id'], unique=False)
    op.create_index(op.f('ix_queue_members_user_id'), 'queue_members', ['user_id'], unique=False)

    agent_presence_status_enum = sa.Enum('available', 'wrap_up', 'offline', name='agent_presence_status_enum')
    agent_presence_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'agent_presence',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('user_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('status', agent_presence_status_enum, nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('wrap_up_until', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_agent_presence_user'),
    )
    op.create_index(op.f('ix_agent_presence_user_id'), 'agent_presence', ['user_id'], unique=True)

    queue_call_outcome_enum = sa.Enum('waiting', 'answered', 'abandoned', 'overflowed', name='queue_call_outcome_enum')
    queue_call_outcome_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'queue_call_logs',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('queue_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('call_sid', sa.String(length=50), nullable=False),
        sa.Column('caller_number', sa.String(length=20), nullable=False),
        sa.Column('phone_number_e164', sa.String(length=20), nullable=True),
        sa.Column('entered_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('agent_user_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('left_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('outcome', queue_call_outcome_enum, nullable=False),
        sa.ForeignKeyConstraint(['queue_id'], ['call_queues.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['agent_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_queue_call_logs_queue_id'), 'queue_call_logs', ['queue_id'], unique=False)
    op.create_index(op.f('ix_queue_call_logs_call_sid'), 'queue_call_logs', ['call_sid'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_queue_call_logs_call_sid'), table_name='queue_call_logs')
    op.drop_index(op.f('ix_queue_call_logs_queue_id'), table_name='queue_call_logs')
    op.drop_table('queue_call_logs')
    sa.Enum(name='queue_call_outcome_enum').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_agent_presence_user_id'), table_name='agent_presence')
    op.drop_table('agent_presence')
    sa.Enum(name='agent_presence_status_enum').drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_queue_members_user_id'), table_name='queue_members')
    op.drop_index(op.f('ix_queue_members_queue_id'), table_name='queue_members')
    op.drop_table('queue_members')

    op.drop_index(op.f('ix_call_queues_account_id'), table_name='call_queues')
    op.drop_table('call_queues')
