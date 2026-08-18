"""seed receptionist callback requested notification template

Revision ID: b52d3c2c6fc3
Revises: 426936f97757
Create Date: 2026-08-18 00:00:01.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b52d3c2c6fc3'
down_revision: Union[str, None] = '426936f97757'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_KEY = "voice.receptionist_callback_requested"


def upgrade() -> None:
    templates_table = sa.table(
        'notification_templates',
        sa.column('id', sa.String),
        sa.column('key', sa.String),
        sa.column('domain', sa.String),
        sa.column('category', sa.String),
        sa.column('priority', sa.String),
        sa.column('subject_template', sa.String),
        sa.column('body_template', sa.Text),
    )
    op.bulk_insert(
        templates_table,
        [
            {
                "id": str(uuid.uuid4()),
                "key": _TEMPLATE_KEY,
                "domain": "VOICE",
                "category": "TRANSACTIONAL",
                "priority": "STANDARD",
                "subject_template": "A caller requested a callback",
                "body_template": (
                    "Hello {user_display_name}, {caller_number} called and requested a callback "
                    "({callback_window}). Check the AI Receptionist log for the full message."
                ),
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM notification_templates WHERE key = :key").bindparams(key=_TEMPLATE_KEY)
    )
