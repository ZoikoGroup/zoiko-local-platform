"""add notification suppression list, per-domain preferences, and extended delivery ledger states

Revision ID: d4a1f6c2b8e3
Revises: 11fe863d9b8a
Create Date: 2026-08-10 15:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'd4a1f6c2b8e3'
down_revision: Union[str, None] = '11fe863d9b8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Email Communications System doc §3.2 "delivery ledger states" - only
    # the subset this platform can actually observe today via Resend's
    # webhooks (see notifications.service.handle_resend_webhook).
    op.execute("ALTER TYPE notification_delivery_status_enum ADD VALUE IF NOT EXISTS 'DELIVERED'")
    op.execute("ALTER TYPE notification_delivery_status_enum ADD VALUE IF NOT EXISTS 'BOUNCED'")
    op.execute("ALTER TYPE notification_delivery_status_enum ADD VALUE IF NOT EXISTS 'COMPLAINED'")
    op.execute("ALTER TYPE notification_delivery_status_enum ADD VALUE IF NOT EXISTS 'CLICKED'")

    op.add_column(
        'notification_deliveries', sa.Column('provider_message_id', sa.String(length=100), nullable=True)
    )
    op.create_index(
        op.f('ix_notification_deliveries_provider_message_id'),
        'notification_deliveries', ['provider_message_id'], unique=False,
    )

    op.add_column(
        'notification_preferences',
        sa.Column(
            'disabled_domains', postgresql.ARRAY(sa.String(length=20)),
            nullable=False, server_default='{}',
        ),
    )

    suppression_reason_enum = postgresql.ENUM(
        'HARD_BOUNCE', 'COMPLAINT', 'MANUAL_UNSUBSCRIBE', name='suppression_reason_enum', create_type=False
    )
    suppression_reason_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'notification_suppressions',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('recipient_email', sa.String(length=255), nullable=False),
        sa.Column('domain', sa.String(length=20), nullable=True),
        sa.Column('reason', suppression_reason_enum, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_notification_suppressions_recipient_email'),
        'notification_suppressions', ['recipient_email'], unique=False,
    )
    op.create_index(
        op.f('ix_notification_suppressions_created_at'),
        'notification_suppressions', ['created_at'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_notification_suppressions_created_at'), table_name='notification_suppressions')
    op.drop_index(op.f('ix_notification_suppressions_recipient_email'), table_name='notification_suppressions')
    op.drop_table('notification_suppressions')
    op.execute('DROP TYPE IF EXISTS suppression_reason_enum')

    op.drop_column('notification_preferences', 'disabled_domains')

    op.drop_index(op.f('ix_notification_deliveries_provider_message_id'), table_name='notification_deliveries')
    op.drop_column('notification_deliveries', 'provider_message_id')

    # No DROP VALUE in Postgres - the added delivery-status enum values are
    # a permanent no-op on downgrade, same as every other enum-extending
    # migration in this codebase.
