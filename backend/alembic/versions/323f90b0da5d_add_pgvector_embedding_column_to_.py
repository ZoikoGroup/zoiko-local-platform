"""add pgvector embedding column to conversation summaries

Revision ID: 323f90b0da5d
Revises: 1976d339ec28
Create Date: 2026-08-04 17:05:24.049450

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = '323f90b0da5d'
down_revision: Union[str, None] = '1976d339ec28'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMBEDDING_DIMENSIONS = 1536  # must match app/integrations/embeddings/cohere.py


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "conversation_summaries", sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True)
    )
    # HNSW over ivfflat - no "lists" tuning parameter needed and performs
    # well without requiring a large pre-existing dataset first, which
    # suits this table's likely size far better.
    op.execute(
        "CREATE INDEX conversation_summaries_embedding_hnsw_idx ON conversation_summaries "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS conversation_summaries_embedding_hnsw_idx")
    op.drop_column("conversation_summaries", "embedding")
    # Extension is left in place on downgrade - other objects/sessions may
    # depend on it, and dropping a shared extension isn't this migration's
    # call to make.
