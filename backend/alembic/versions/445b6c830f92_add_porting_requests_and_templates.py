"""add porting requests and templates

Revision ID: 445b6c830f92
Revises: 0f13fd762e51
Create Date: 2026-08-04 10:50:05.350426

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '445b6c830f92'
down_revision: Union[str, None] = '0f13fd762e51'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

porting_request_status_enum = postgresql.ENUM(
    "SUBMITTED", "APPROVED", "REJECTED", "COMPLETED", "CANCELED",
    name="porting_request_status_enum", create_type=False,
)

# Number Porting - the last of the marketing-site gaps identified against
# the real live site (see project memory). Scoped down: the actual carrier
# hand-off is a manual staff action (see app/porting/service.py's
# docstring), so these 4 templates are just intake/status notifications -
# email-only, same as most of this registry.
_TEMPLATES = [
    (
        "porting.submitted",
        "TRANSACTIONAL",
        "We've received your request to port {phone_number}",
        "We've received your request to port {phone_number} to Zoiko Local. "
        "Our team will review it and follow up with next steps.",
    ),
    (
        "porting.approved",
        "TRANSACTIONAL",
        "Your porting request for {phone_number} is approved",
        "Your request to port {phone_number} has been approved and is now being processed "
        "with your current carrier. We'll notify you once it's complete.",
    ),
    (
        "porting.rejected",
        "TRANSACTIONAL",
        "Your porting request for {phone_number} needs attention",
        "Your request to port {phone_number} could not be processed.{reason_line} "
        "Please contact support or submit a new request with corrected details.",
    ),
    (
        "porting.completed",
        "TRANSACTIONAL",
        "{phone_number} is now active on Zoiko Local",
        "Your number {phone_number} has finished porting and is now active on your account. "
        "You can start making and receiving calls on it right away.",
    ),
]


def upgrade() -> None:
    porting_request_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "porting_requests",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("phone_number", sa.String(length=20), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("current_carrier", sa.String(length=255), nullable=False),
        sa.Column("carrier_account_number", sa.String(length=100), nullable=False),
        sa.Column("billing_name", sa.String(length=255), nullable=False),
        sa.Column("billing_address", sa.Text(), nullable=False),
        sa.Column("status", porting_request_status_enum, nullable=False, server_default="SUBMITTED"),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("twilio_incoming_number_sid", sa.String(length=50), nullable=True),
        sa.Column(
            "created_number_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("phone_numbers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.alter_column("porting_requests", "status", server_default=None)
    op.create_index("ix_porting_requests_account_id", "porting_requests", ["account_id"])
    op.create_index("ix_porting_requests_phone_number", "porting_requests", ["phone_number"])

    templates_table = sa.table(
        "notification_templates",
        sa.column("id", postgresql.UUID(as_uuid=False)),
        sa.column("key", sa.String),
        sa.column("category", sa.String),
        sa.column("subject_template", sa.String),
        sa.column("body_template", sa.Text),
    )
    op.bulk_insert(
        templates_table,
        [
            {"id": str(uuid.uuid4()), "key": key, "category": category, "subject_template": subject, "body_template": body}
            for key, category, subject, body in _TEMPLATES
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM notification_templates WHERE key IN " + str(tuple(t[0] for t in _TEMPLATES)))
    op.drop_index("ix_porting_requests_phone_number", table_name="porting_requests")
    op.drop_index("ix_porting_requests_account_id", table_name="porting_requests")
    op.drop_table("porting_requests")
    porting_request_status_enum.drop(op.get_bind(), checkfirst=True)
