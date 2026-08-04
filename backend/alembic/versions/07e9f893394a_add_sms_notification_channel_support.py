"""add sms notification channel support

Revision ID: 07e9f893394a
Revises: ab687051413a
Create Date: 2026-08-03 22:17:06.112467

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '07e9f893394a'
down_revision: Union[str, None] = 'ab687051413a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

notification_channel_enum = postgresql.ENUM("EMAIL", "SMS", name="notification_channel_enum", create_type=False)

# Short, SMS-appropriate body for the one safety-critical event that gets
# a second channel - see notifications/service.py's notify_number_suspended.
_SUSPENDED_SMS_BODY = "Zoiko Local: {e164} has been suspended.{reason_line}"


def upgrade() -> None:
    op.add_column("users", sa.Column("phone_number", sa.String(length=20), nullable=True))

    op.add_column("notification_templates", sa.Column("sms_body_template", sa.Text(), nullable=True))

    notification_channel_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "notification_deliveries",
        sa.Column(
            "channel", notification_channel_enum, nullable=False, server_default="EMAIL",
        ),
    )
    op.alter_column("notification_deliveries", "channel", server_default=None)
    op.alter_column("notification_deliveries", "recipient_email", nullable=True)
    op.add_column("notification_deliveries", sa.Column("recipient_phone", sa.String(length=20), nullable=True))

    op.execute(
        sa.text("UPDATE notification_templates SET sms_body_template = :body WHERE key = 'number.suspended'").
        bindparams(body=_SUSPENDED_SMS_BODY)
    )


def downgrade() -> None:
    op.drop_column("notification_deliveries", "recipient_phone")
    op.alter_column("notification_deliveries", "recipient_email", nullable=False)
    op.drop_column("notification_deliveries", "channel")
    notification_channel_enum.drop(op.get_bind(), checkfirst=True)
    op.drop_column("notification_templates", "sms_body_template")
    op.drop_column("users", "phone_number")
