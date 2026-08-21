"""add compliance_cases.number_id foreign key to phone_numbers

Revision ID: 1a42d011d8c7
Revises: e590db9f87e6
Create Date: 2026-08-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '1a42d011d8c7'
down_revision: Union[str, None] = 'e590db9f87e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # number_id was a plain, unvalidated varchar(255) - open_compliance_case
    # now checks ownership before insert (see compliance/service.py), and
    # this makes that guarantee a real DB constraint too. Safe as a straight
    # type change: no production account has ever populated this column
    # (verified against the live DB before writing this migration). Any
    # non-UUID-shaped leftover is nulled out rather than deleting the case
    # row - the case's own audit/decision history must survive regardless.
    op.execute("UPDATE compliance_cases SET number_id = NULL WHERE number_id IS NOT NULL AND number_id !~ '^[0-9a-fA-F-]{36}$'")
    op.alter_column(
        "compliance_cases", "number_id",
        existing_type=sa.String(length=255),
        type_=postgresql.UUID(as_uuid=False),
        postgresql_using="number_id::uuid",
        nullable=True,
    )
    # Same reasoning as the non-UUID cleanup above - an orphaned reference
    # (number_id shaped like a UUID but not a real phone_numbers row) would
    # otherwise make create_foreign_key fail outright.
    op.execute(
        "UPDATE compliance_cases cc SET number_id = NULL "
        "WHERE cc.number_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM phone_numbers pn WHERE pn.id = cc.number_id)"
    )
    op.create_foreign_key(
        "fk_compliance_cases_number_id_phone_numbers",
        "compliance_cases", "phone_numbers",
        ["number_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_compliance_cases_number_id_phone_numbers", "compliance_cases", type_="foreignkey")
    op.alter_column(
        "compliance_cases", "number_id",
        existing_type=postgresql.UUID(as_uuid=False),
        type_=sa.String(length=255),
        nullable=True,
    )
