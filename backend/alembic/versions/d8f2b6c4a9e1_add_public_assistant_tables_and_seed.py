"""add public website assistant tables + seed content (WF-01..WF-13)

Revision ID: d8f2b6c4a9e1
Revises: c8a4e1f0d726
Create Date: 2026-11-11

The public assistant module (app/assistant/) per the 13 chatbot
engineering wireframes (v1.0, 24 October 2026). Five tables - see
app/assistant/models.py docstrings for the design rationale of each.

Seed content is imported from app.assistant.content - the single source
of truth shared with tests/test_chatbot.py, which seeds the same rows
via the ORM because the test schema is built with
Base.metadata.create_all rather than by running migrations. This is the
first migration in this repo to import app code, but env.py already
imports every app model (and app.core.config) at startup, so the import
context is identical; keeping the content in one place beats duplicating
long copy decks into raw SQL strings where they would silently drift
from what tests exercise.

Every seed row is idempotent on its natural key (intent_id / slug /
destination_key / category+pattern), so re-running against a partially
seedled DB is a no-op, and downgrade removes exactly what this revision
added (by the same keys), never anything a staff member later edited.

Naming (WF docs): the assistant's display name is the configured string
"Zoiko Local Assistant"; "Locu" is an internal working name that must
never reach a customer-facing surface.
"""
import json
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8f2b6c4a9e1'
down_revision: Union[str, None] = 'c8a4e1f0d726'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

from app.assistant.content import (  # noqa: E402
    CONTENT_VERSION,
    DESTINATIONS,
    INTENTS,
    KNOWLEDGE_SOURCES,
    SAFETY_RULES,
)


def upgrade() -> None:
    op.create_table(
        'assistant_intents',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('intent_id', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=100), nullable=False),
        sa.Column('description', sa.String(length=200), nullable=False),
        sa.Column('keywords', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_quick_start', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('intent_id', name='uq_assistant_intent_id'),
    )
    op.create_index('ix_assistant_intents_intent_id', 'assistant_intents', ['intent_id'], unique=True)

    op.create_table(
        'assistant_knowledge_sources',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('slug', sa.String(length=100), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column('publisher', sa.String(length=120), nullable=False),
        sa.Column('canonical_url', sa.String(length=500), nullable=False),
        sa.Column(
            'authority_class',
            sa.Enum(
                'A0', 'A1', 'A2', 'A3', 'A4',
                name='assistant_authority_class_enum',
            ),
            nullable=False,
        ),
        sa.Column('intent_id', sa.String(length=50), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('content_version', sa.String(length=30), nullable=False),
        sa.Column('market', sa.String(length=10), nullable=False, server_default='GLOBAL'),
        sa.Column('effective_from', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug', name='uq_assistant_knowledge_slug'),
    )
    op.create_index('ix_assistant_knowledge_sources_slug', 'assistant_knowledge_sources', ['slug'], unique=True)
    op.create_index('ix_assistant_knowledge_sources_intent_id', 'assistant_knowledge_sources', ['intent_id'])

    op.create_table(
        'assistant_safety_rules',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            'category',
            sa.Enum('URGENT', 'SENSITIVE', name='assistant_safety_category_enum'),
            nullable=False,
        ),
        sa.Column('pattern', sa.Text(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_assistant_safety_rules_category', 'assistant_safety_rules', ['category'])

    op.create_table(
        'assistant_destinations',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('destination_key', sa.String(length=50), nullable=False),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column('url', sa.String(length=500), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('destination_key', name='uq_assistant_destination_key'),
    )
    op.create_index('ix_assistant_destinations_destination_key', 'assistant_destinations', ['destination_key'], unique=True)

    op.create_table(
        'assistant_sessions',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('session_token_hash', sa.String(length=64), nullable=False),
        sa.Column('page_context', sa.String(length=200), nullable=True),
        sa.Column('locale', sa.String(length=10), nullable=False),
        sa.Column('flow_context', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('message_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_token_hash', name='uq_assistant_session_token_hash'),
    )
    op.create_index('ix_assistant_sessions_session_token_hash', 'assistant_sessions', ['session_token_hash'], unique=True)
    op.create_index('ix_assistant_sessions_expires_at', 'assistant_sessions', ['expires_at'])

    conn = op.get_bind()
    now = conn.execute(sa.text("SELECT now()")).scalar()

    for intent in INTENTS:
        exists = conn.execute(
            sa.text("SELECT 1 FROM assistant_intents WHERE intent_id = :k"),
            {"k": intent["intent_id"]},
        ).scalar()
        if exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO assistant_intents (
                    id, intent_id, title, description, keywords, sort_order,
                    is_quick_start, is_active, created_at
                ) VALUES (
                    :id, :intent_id, :title, :description, CAST(:keywords AS jsonb),
                    :sort_order, :is_quick_start, true, :now
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "intent_id": intent["intent_id"],
                "title": intent["title"],
                "description": intent["description"],
                "keywords": json.dumps(intent.get("keywords") or []),
                "sort_order": intent["sort_order"],
                "is_quick_start": intent["is_quick_start"],
                "now": now,
            },
        )

    for source in KNOWLEDGE_SOURCES:
        exists = conn.execute(
            sa.text("SELECT 1 FROM assistant_knowledge_sources WHERE slug = :k"),
            {"k": source["slug"]},
        ).scalar()
        if exists:
            continue
        # authority_class rows in content.py carry the WF-11 wireframe's
        # "a3_product_content" style labels; the SQL enum stores the
        # SQLAlchemy member names (A0..A4), so take the label's prefix.
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

    for rule in SAFETY_RULES:
        exists = conn.execute(
            sa.text(
                "SELECT 1 FROM assistant_safety_rules WHERE category = :c AND pattern = :p"
            ),
            {"c": rule["category"].upper(), "p": rule["pattern"]},
        ).scalar()
        if exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO assistant_safety_rules (
                    id, category, pattern, sort_order, is_active, created_at
                ) VALUES (
                    :id, CAST(:category AS assistant_safety_category_enum),
                    :pattern, :sort_order, true, :now
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "category": rule["category"].upper(),
                "pattern": rule["pattern"],
                "sort_order": rule["sort_order"],
                "now": now,
            },
        )

    for destination in DESTINATIONS:
        exists = conn.execute(
            sa.text("SELECT 1 FROM assistant_destinations WHERE destination_key = :k"),
            {"k": destination["destination_key"]},
        ).scalar()
        if exists:
            continue
        conn.execute(
            sa.text(
                """
                INSERT INTO assistant_destinations (
                    id, destination_key, label, url, is_active, created_at
                ) VALUES (:id, :destination_key, :label, :url, true, :now)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "destination_key": destination["destination_key"],
                "label": destination["label"],
                "url": destination["url"],
                "now": now,
            },
        )


def downgrade() -> None:
    conn = op.get_bind()
    # Remove only what this revision seeded (by natural key), then drop
    # the tables and their enums.
    conn.execute(
        sa.text(
            "DELETE FROM assistant_safety_rules WHERE pattern = ANY(CAST(:patterns AS text[]))"
        ),
        {"patterns": [r["pattern"] for r in SAFETY_RULES]},
    )
    conn.execute(
        sa.text("DELETE FROM assistant_knowledge_sources WHERE slug = ANY(CAST(:slugs AS text[]))"),
        {"slugs": [s["slug"] for s in KNOWLEDGE_SOURCES]},
    )
    conn.execute(
        sa.text("DELETE FROM assistant_intents WHERE intent_id = ANY(CAST(:ids AS text[]))"),
        {"ids": [i["intent_id"] for i in INTENTS]},
    )
    conn.execute(
        sa.text("DELETE FROM assistant_destinations WHERE destination_key = ANY(CAST(:keys AS text[]))"),
        {"keys": [d["destination_key"] for d in DESTINATIONS]},
    )

    op.drop_table('assistant_sessions')
    op.drop_index('ix_assistant_destinations_destination_key', table_name='assistant_destinations')
    op.drop_table('assistant_destinations')
    op.drop_index('ix_assistant_safety_rules_category', table_name='assistant_safety_rules')
    op.drop_table('assistant_safety_rules')
    op.drop_index('ix_assistant_knowledge_sources_intent_id', table_name='assistant_knowledge_sources')
    op.drop_index('ix_assistant_knowledge_sources_slug', table_name='assistant_knowledge_sources')
    op.drop_table('assistant_knowledge_sources')
    op.drop_index('ix_assistant_intents_intent_id', table_name='assistant_intents')
    op.drop_table('assistant_intents')
    sa.Enum(name='assistant_safety_category_enum').drop(op.get_bind(), checkfirst=True)
    sa.Enum(name='assistant_authority_class_enum').drop(op.get_bind(), checkfirst=True)
