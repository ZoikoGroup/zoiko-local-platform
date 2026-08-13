"""seed video guest waiting notification template

Revision ID: 62980457ed92
Revises: 356a23e1135f
Create Date: 2026-08-11 14:02:05.483629

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '62980457ed92'
down_revision: Union[str, None] = '356a23e1135f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_KEY = "video.guest_waiting"


def upgrade() -> None:
    templates_table = sa.table(
        'notification_templates',
        sa.column('id', sa.String),
        sa.column('key', sa.String),
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
                "category": "TRANSACTIONAL",
                "priority": "STANDARD",
                "subject_template": "Someone is waiting to join your Zoiko Local call",
                "body_template": (
                    "Hello {user_display_name}, {video_guest_display_name} is waiting to join your "
                    "video call ({video_room_name}). Open the call to let them in."
                ),
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM notification_templates WHERE key = :key").bindparams(key=_TEMPLATE_KEY)
    )
