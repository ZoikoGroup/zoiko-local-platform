"""fix incidents/status_subscriptions timestamp columns missing NOT NULL

Revision ID: 6bda3e0f8c15
Revises: 351fca0d8b24
Create Date: 2026-08-13 00:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6bda3e0f8c15'
down_revision: Union[str, None] = '351fca0d8b24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Found via a full model-vs-DB nullability audit: b8d4e2f6a1c9's raw
# sa.Column(..., server_default=sa.func.now()) calls for these three
# columns omitted nullable=False, even though app.ops.models.Incident/
# StatusSubscription both declare them as Mapped[datetime] (non-Optional -
# SQLAlchemy 2.0 infers NOT NULL from that at the ORM level), so the model
# and the live schema disagreed. Confirmed live both tables are empty (0
# rows) before this ran, so SET NOT NULL needed no backfill.


def upgrade() -> None:
    op.alter_column('incidents', 'started_at', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('incidents', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('status_subscriptions', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    op.alter_column('status_subscriptions', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column('incidents', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column('incidents', 'started_at', existing_type=sa.DateTime(timezone=True), nullable=True)
