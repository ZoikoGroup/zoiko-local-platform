"""add notification templates and delivery ledger

Revision ID: f177a715ba1a
Revises: f12d9f227da0
Create Date: 2026-08-03 16:20:47.357471

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'f177a715ba1a'
down_revision: Union[str, None] = 'f12d9f227da0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

notification_category_enum = postgresql.ENUM(
    "TRANSACTIONAL", "SECURITY", name="notification_category_enum", create_type=False
)
notification_delivery_status_enum = postgresql.ENUM(
    "SENT", "FAILED", name="notification_delivery_status_enum", create_type=False
)

# The scaled-down "template registry" from the Email Communications System
# doc - just the 5 emails this platform actually sends today, migrated off
# hardcoded strings at each call site. Category values are the Python
# enum's member NAMES (uppercase), matching this codebase's established
# convention for Postgres enum labels (see compliance_case_status_enum -
# stored as PENDING/APPROVED/etc, not pending/approved).
_TEMPLATES = [
    (
        "number.activated",
        "TRANSACTIONAL",
        "{e164} is active on Zoiko Local",
        "Your number {e164} is now active. You can start making and receiving calls.",
    ),
    (
        "number.suspended",
        "TRANSACTIONAL",
        "{e164} has been suspended",
        "Your number {e164} has been suspended.{reason_line}",
    ),
    (
        "compliance.approved",
        "TRANSACTIONAL",
        "Your {jurisdiction} verification has been approved",
        "Good news — your {requirement_type} verification for {jurisdiction} has been approved. "
        "You can now purchase numbers in that country.",
    ),
    (
        "compliance.rejected",
        "TRANSACTIONAL",
        "Your {jurisdiction} verification needs attention",
        "Your {requirement_type} verification for {jurisdiction} was not approved.{reason_line} "
        "Please submit updated documents to try again.",
    ),
    (
        "team_member.added",
        "SECURITY",
        "You've been added to {account_name} on Zoiko Local",
        "You've been added to {account_name} as {role}. Sign in with the email and password you were given.",
    ),
]


def upgrade() -> None:
    notification_category_enum.create(op.get_bind(), checkfirst=True)
    notification_delivery_status_enum.create(op.get_bind(), checkfirst=True)

    templates_table = op.create_table(
        "notification_templates",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("category", notification_category_enum, nullable=False),
        sa.Column("subject_template", sa.String(length=255), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notification_templates_key", "notification_templates", ["key"], unique=True)

    op.create_table(
        "notification_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("event_name", sa.String(length=100), nullable=False),
        sa.Column("recipient_email", sa.String(length=255), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("status", notification_delivery_status_enum, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_notification_deliveries_account_id", "notification_deliveries", ["account_id"])
    op.create_index("ix_notification_deliveries_event_name", "notification_deliveries", ["event_name"])

    op.bulk_insert(
        templates_table,
        [
            {"id": str(uuid.uuid4()), "key": key, "category": category, "subject_template": subject, "body_template": body}
            for key, category, subject, body in _TEMPLATES
        ],
    )


def downgrade() -> None:
    op.drop_table("notification_deliveries")
    op.drop_table("notification_templates")
    notification_delivery_status_enum.drop(op.get_bind(), checkfirst=True)
    notification_category_enum.drop(op.get_bind(), checkfirst=True)
