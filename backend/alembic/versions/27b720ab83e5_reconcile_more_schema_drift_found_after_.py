"""reconcile more schema drift found after merging anilupdated: created_at not null

Revision ID: 27b720ab83e5
Revises: e1b9d7fa8028
Create Date: 2026-08-21 10:47:39.618517

Same recurring pattern 9ddae52d4dd6 already fixed once for an earlier
anilupdated merge ("rate created_at not null") - ai_receptionist_addon_rates
and caller_identities (both new tables from this merge) were created with
created_at nullable, even though their models declare a non-Optional
Mapped[datetime] (implying NOT NULL). Confirmed live via `alembic check`
plus a direct row count that no existing row has a NULL value, so this is
safe to tighten. NOT fixing caller_identities' phone_number_id unique
constraint here despite `alembic check` also flagging it - confirmed via
direct inspection that caller_identities_phone_number_id_key already exists
and correctly enforces uniqueness in the real database; the flag is a
naming-convention comparison quirk in autogenerate, not an actual gap.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '27b720ab83e5'
down_revision: Union[str, None] = 'e1b9d7fa8028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('ai_receptionist_addon_rates', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=False)
    op.alter_column('caller_identities', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=False)


def downgrade() -> None:
    op.alter_column('caller_identities', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=True)
    op.alter_column('ai_receptionist_addon_rates', 'created_at', existing_type=sa.DateTime(timezone=True), nullable=True)
