"""add read_at to notification deliveries

Revision ID: 141799fdba9b
Revises: 323f90b0da5d
Create Date: 2026-08-04 17:37:11.270767

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '141799fdba9b'
down_revision: Union[str, None] = '323f90b0da5d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # CORRECTION: this and a3f5c9d2e148 (the parallel/venky-branch migration
    # this comment originally deferred to) BOTH assumed the other actually
    # added this column - neither did, so a genuinely fresh chain never
    # creates notification_deliveries.read_at at all, same "migration
    # stamped past without its DDL actually running" root cause as the
    # fraud_rules/fraud_cases gap fixed in 7a2e5c918bf4. Confirmed live
    # running this chain against a fresh database. Guarded with has_column()
    # so it's correct regardless of which of the two migrations a future
    # chain edit runs first, and a no-op wherever the column already exists.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("notification_deliveries")}
    if "read_at" not in columns:
        op.add_column("notification_deliveries", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    pass
