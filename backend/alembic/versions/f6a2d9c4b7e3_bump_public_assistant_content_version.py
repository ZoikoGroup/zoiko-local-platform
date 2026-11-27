from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.assistant.content import CONTENT_VERSION

revision: str = "f6a2d9c4b7e3"
down_revision: Union[str, None] = "f6a2d9c4b7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PREVIOUS_CONTENT_VERSION = "wf-2026-11-19.2"


def upgrade() -> None:
    conn = op.get_bind()
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
        sa.text(
            """
            UPDATE assistant_knowledge_sources
            SET content_version = :previous
            WHERE content_version = :version
            """
        ),
        {"previous": _PREVIOUS_CONTENT_VERSION, "version": CONTENT_VERSION},
    )
