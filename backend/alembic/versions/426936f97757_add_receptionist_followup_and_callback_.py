"""add receptionist followup and callback fields

Revision ID: 426936f97757
Revises: 0684d36b52b2
Create Date: 2026-08-18 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '426936f97757'
down_revision: Union[str, None] = '0684d36b52b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'receptionist_calls', sa.Column('callback_preference', sa.String(length=200), nullable=True)
    )
    op.add_column(
        'receptionist_calls',
        sa.Column('followup_asked', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        'receptionist_calls',
        sa.Column('callback_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    # Postgres enum TYPE must exist before a column can reference it -
    # op.add_column alone doesn't create it for an existing table (same fix
    # as summary_urgency_enum's 82089d7b864c migration).
    window_enum = sa.Enum('ASAP', 'TODAY', 'TOMORROW', name='receptionist_callback_window_enum')
    window_enum.create(op.get_bind(), checkfirst=True)
    op.add_column('receptionist_calls', sa.Column('callback_window', window_enum, nullable=True))


def downgrade() -> None:
    op.drop_column('receptionist_calls', 'callback_window')
    sa.Enum(name='receptionist_callback_window_enum').drop(op.get_bind(), checkfirst=True)
    op.drop_column('receptionist_calls', 'callback_requested')
    op.drop_column('receptionist_calls', 'followup_asked')
    op.drop_column('receptionist_calls', 'callback_preference')
