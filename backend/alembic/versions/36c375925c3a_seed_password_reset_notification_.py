"""seed password reset notification template

Revision ID: 36c375925c3a
Revises: cdc47723ab0f
Create Date: 2026-08-06 00:08:14.815001

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '36c375925c3a'
down_revision: Union[str, None] = 'cdc47723ab0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TEMPLATE_KEY = "auth.password_reset"


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
                # SECURITY + CRITICAL - an account-access-changing action
                # must always send regardless of quiet hours or the
                # transactional-email opt-out (see notifications/service.py's
                # _is_exempt_from_suppression).
                "category": "SECURITY",
                "priority": "CRITICAL",
                "subject_template": "Reset your Zoiko Local password",
                "body_template": (
                    "We received a request to reset your Zoiko Local password. "
                    "Click the link below to choose a new one - this link expires in 30 minutes "
                    "and can only be used once.\n\n{reset_url}\n\n"
                    "If you didn't request this, you can safely ignore this email - "
                    "your password won't be changed."
                ),
            },
        ],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM notification_templates WHERE key = :key").bindparams(key=_TEMPLATE_KEY)
    )
