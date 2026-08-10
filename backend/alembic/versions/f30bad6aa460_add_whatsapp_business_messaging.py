"""add whatsapp_enabled to phone_numbers and messaging_conversations/messaging_messages (WhatsApp Business integration, Phase 3)

Revision ID: f30bad6aa460
Revises: 8598eaf03f33
Create Date: 2026-08-06 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f30bad6aa460'
down_revision: Union[str, None] = '8598eaf03f33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('phone_numbers', sa.Column('whatsapp_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column('phone_numbers', 'whatsapp_enabled', server_default=None)

    # Uppercase labels match this codebase's convention of storing a Python
    # (str, Enum)'s *member name* in Postgres, since SQLAlchemy's Enum(...)
    # binds by .name (not .value) when given a Python enum class.
    messaging_channel_enum = postgresql.ENUM('WHATSAPP', 'SMS', name='messaging_channel_enum', create_type=False)
    messaging_channel_enum.create(op.get_bind(), checkfirst=True)
    message_direction_enum = postgresql.ENUM('INBOUND', 'OUTBOUND', name='message_direction_enum', create_type=False)
    message_direction_enum.create(op.get_bind(), checkfirst=True)
    message_status_enum = postgresql.ENUM('QUEUED', 'SENT', 'DELIVERED', 'FAILED', 'RECEIVED', name='message_status_enum', create_type=False)
    message_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'messaging_conversations',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('phone_number_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('customer_number', sa.String(length=20), nullable=False),
        sa.Column('channel', messaging_channel_enum, nullable=False),
        sa.Column('opted_out', sa.Boolean(), nullable=False),
        sa.Column('opted_out_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_message_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['phone_number_id'], ['phone_numbers.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('phone_number_id', 'customer_number', 'channel', name='uq_conversation_thread'),
    )
    op.create_index(op.f('ix_messaging_conversations_account_id'), 'messaging_conversations', ['account_id'], unique=False)
    op.create_index(op.f('ix_messaging_conversations_phone_number_id'), 'messaging_conversations', ['phone_number_id'], unique=False)

    op.create_table(
        'messaging_messages',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('conversation_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('direction', message_direction_enum, nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('provider_message_sid', sa.String(length=50), nullable=True),
        sa.Column('status', message_status_enum, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['conversation_id'], ['messaging_conversations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_messaging_messages_conversation_id'), 'messaging_messages', ['conversation_id'], unique=False)
    op.create_index(op.f('ix_messaging_messages_provider_message_sid'), 'messaging_messages', ['provider_message_sid'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_messaging_messages_provider_message_sid'), table_name='messaging_messages')
    op.drop_index(op.f('ix_messaging_messages_conversation_id'), table_name='messaging_messages')
    op.drop_table('messaging_messages')

    op.drop_index(op.f('ix_messaging_conversations_phone_number_id'), table_name='messaging_conversations')
    op.drop_index(op.f('ix_messaging_conversations_account_id'), table_name='messaging_conversations')
    op.drop_table('messaging_conversations')

    postgresql.ENUM(name='message_status_enum', create_type=False).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='message_direction_enum', create_type=False).drop(op.get_bind(), checkfirst=True)
    postgresql.ENUM(name='messaging_channel_enum', create_type=False).drop(op.get_bind(), checkfirst=True)

    op.drop_column('phone_numbers', 'whatsapp_enabled')
