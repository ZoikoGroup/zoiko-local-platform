"""add legal hold to accounts and erasure requests table

Revision ID: c3b0f40f4bc1
Revises: 98ac3783df0b
Create Date: 2026-08-22 18:10:29.864097

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c3b0f40f4bc1'
down_revision: Union[str, None] = '98ac3783df0b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_GRANTS: list[tuple[str, str]] = [
    ("accounts.manage_legal_hold", "SUPER_ADMIN"),
    ("retention.resolve_erasure_requests", "SUPER_ADMIN"),
]


def upgrade() -> None:
    op.add_column("accounts", sa.Column("legal_hold", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("accounts", sa.Column("legal_hold_reference", sa.String(length=100), nullable=True))

    erasure_status_enum = postgresql.ENUM("PENDING", "COMPLETED", "REJECTED", name="erasure_request_status_enum")
    erasure_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "erasure_requests",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("account_id", UUID(as_uuid=False), sa.ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("requested_by", UUID(as_uuid=False), nullable=False),
        sa.Column("status", erasure_status_enum, nullable=False, server_default="PENDING"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("resolved_by", sa.String(length=100), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_erasure_requests_account_id", "erasure_requests", ["account_id"])

    grants_table = sa.table(
        "staff_capability_grants",
        sa.column("id", sa.String),
        sa.column("capability", sa.String),
        sa.column("role", sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": capability, "role": role} for capability, role in _GRANTS],
    )


def downgrade() -> None:
    conn = op.get_bind()
    for capability, role in _GRANTS:
        conn.execute(
            sa.text("DELETE FROM staff_capability_grants WHERE capability = :capability AND role = :role"),
            {"capability": capability, "role": role},
        )
    op.drop_index("ix_erasure_requests_account_id", table_name="erasure_requests")
    op.drop_table("erasure_requests")
    postgresql.ENUM(name="erasure_request_status_enum").drop(op.get_bind(), checkfirst=True)
    op.drop_column("accounts", "legal_hold_reference")
    op.drop_column("accounts", "legal_hold")
