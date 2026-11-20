from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.platform_assistant.content import CONTENT_ROWS, CONTENT_VERSION

revision: str = "a9d4e1c7f2b8"
down_revision: Union[str, None] = "d8f2b6c4a9e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "platform_assistant_sessions",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("session_token_hash", sa.String(length=64), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("context_key", sa.String(length=80), nullable=True),
        sa.Column("context_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("invalidation_reason", sa.String(length=60), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_token_hash", name="uq_platform_assistant_session_token_hash"),
    )
    op.create_index("ix_platform_assistant_sessions_user_id", "platform_assistant_sessions", ["user_id"])
    op.create_index("ix_platform_assistant_sessions_account_id", "platform_assistant_sessions", ["account_id"])
    op.create_index("ix_platform_assistant_sessions_session_token_hash", "platform_assistant_sessions", ["session_token_hash"], unique=True)
    op.create_index("ix_platform_assistant_sessions_expires_at", "platform_assistant_sessions", ["expires_at"])

    op.create_table(
        "platform_assistant_content",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("content_key", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_version", sa.String(length=30), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("content_key", name="uq_platform_assistant_content_key"),
    )
    op.create_index("ix_platform_assistant_content_content_key", "platform_assistant_content", ["content_key"], unique=True)

    op.create_table(
        "platform_assistant_events",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("session_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("module", sa.String(length=40), nullable=False),
        sa.Column("risk_tier", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("destination_key", sa.String(length=60), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_platform_assistant_events_session_id", "platform_assistant_events", ["session_id"])
    op.create_index("ix_platform_assistant_events_user_id", "platform_assistant_events", ["user_id"])
    op.create_index("ix_platform_assistant_events_account_id", "platform_assistant_events", ["account_id"])
    op.create_index("ix_platform_assistant_events_event_type", "platform_assistant_events", ["event_type"])
    op.create_index("ix_platform_assistant_events_request_id", "platform_assistant_events", ["request_id"])

    conn = op.get_bind()
    now = conn.execute(sa.text("SELECT now()")).scalar()
    for row in CONTENT_ROWS:
        exists = conn.execute(
            sa.text("SELECT 1 FROM platform_assistant_content WHERE content_key = :key"),
            {"key": row["key"]},
        ).scalar()
        if exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO platform_assistant_content (
                    id, content_key, title, category, body, content_version,
                    is_active, created_at
                ) VALUES (
                    :id, :content_key, :title, :category, :body, :content_version,
                    true, :now
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "content_key": row["key"],
                "title": row["title"],
                "category": row["category"],
                "body": row["body"],
                "content_version": CONTENT_VERSION,
                "now": now,
            },
        )


def downgrade() -> None:
    op.drop_table("platform_assistant_events")
    op.drop_table("platform_assistant_content")
    op.drop_table("platform_assistant_sessions")
