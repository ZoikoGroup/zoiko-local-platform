from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

from app.assistant.content import CONTENT_VERSION, KNOWLEDGE_SOURCES

revision: str = "f6a2d9c4b7e2"
down_revision: Union[str, None] = "f6a2d9c4b7e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_SOURCE_SLUG = "contact-support-page"
_PREVIOUS_CONTENT_VERSION = "wf-2026-11-19.1"


def upgrade() -> None:
    conn = op.get_bind()
    now = conn.execute(sa.text("SELECT now()")).scalar()

    source = next(row for row in KNOWLEDGE_SOURCES if row["slug"] == _SOURCE_SLUG)
    if not conn.execute(
        sa.text("SELECT 1 FROM assistant_knowledge_sources WHERE slug = :slug"),
        {"slug": _SOURCE_SLUG},
    ).scalar():
        authority = source["authority_class"].split("_", 1)[0].upper()
        conn.execute(
            sa.text(
                """
                INSERT INTO assistant_knowledge_sources (
                    id, slug, title, publisher, canonical_url, authority_class,
                    intent_id, body, content_version, market, effective_from,
                    reviewed_at, is_active, created_at
                ) VALUES (
                    :id, :slug, :title, 'Zoiko Local', :canonical_url,
                    CAST(:authority AS assistant_authority_class_enum),
                    :intent_id, :body, :content_version, 'GLOBAL', :now, :now, true, :now
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "slug": source["slug"],
                "title": source["title"],
                "canonical_url": source["canonical_url"],
                "authority": authority,
                "intent_id": source["intent_id"],
                "body": source["body"],
                "content_version": CONTENT_VERSION,
                "now": now,
            },
        )

    conn.execute(
        sa.text(
            """
            UPDATE assistant_knowledge_sources
            SET content_version = :version
            WHERE content_version = :previous
            """
        ),
        {"version": CONTENT_VERSION, "previous": _PREVIOUS_CONTENT_VERSION},
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM assistant_knowledge_sources WHERE slug = :slug"),
        {"slug": _SOURCE_SLUG},
    )
    conn.execute(
        sa.text(
            """
            UPDATE assistant_knowledge_sources
            SET content_version = :previous
            WHERE content_version = :version
            """
        ),
        {"previous": _PREVIOUS_CONTENT_VERSION, "version": CONTENT_VERSION},
    )
