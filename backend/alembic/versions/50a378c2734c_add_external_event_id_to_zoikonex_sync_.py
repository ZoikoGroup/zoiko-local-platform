"""add external event id to zoikonex sync events

Revision ID: 50a378c2734c
Revises: b292ad2dad84
Create Date: 2026-08-06 15:02:48.252253

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '50a378c2734c'
down_revision: Union[str, None] = 'b292ad2dad84'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("zoikonex_sync_events", sa.Column("external_event_id", sa.String(length=100), nullable=True))
    op.create_index(
        op.f("ix_zoikonex_sync_events_external_event_id"),
        "zoikonex_sync_events",
        ["external_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_zoikonex_sync_events_external_event_id"), table_name="zoikonex_sync_events")
    op.drop_column("zoikonex_sync_events", "external_event_id")
