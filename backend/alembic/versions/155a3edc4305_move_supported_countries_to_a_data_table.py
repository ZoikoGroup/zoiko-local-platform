"""move supported countries to a data table

Revision ID: 155a3edc4305
Revises: 6f9bce3448b8
Create Date: 2026-08-10 12:39:59.180788

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '155a3edc4305'
down_revision: Union[str, None] = '6f9bce3448b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Commercial Billing Operating Standard doc §19 names a hardcoded
# country-availability list as a P0 launch blocker, same rule it applies to
# plan names and prices - this backfills the exact 8 countries that used to
# live in the now-deleted app/numbering/numbers/countries.py Python
# constant, so existing behavior (and existing tests) don't change, only
# where the list lives.
_SEED_COUNTRIES = [
    ("US", "United States", 0),
    ("CA", "Canada", 1),
    ("GB", "United Kingdom", 2),
    ("AU", "Australia", 3),
    ("DE", "Germany", 4),
    ("FR", "France", 5),
    ("IN", "India", 6),
    ("SG", "Singapore", 7),
]


def upgrade() -> None:
    # Note: autogenerate also picked up pre-existing, unrelated drift (an
    # 'agent_presence' unique-constraint name and 'calling_rates'
    # nullability/constraint-naming mismatch between the live DB and the
    # models) - deliberately left out of this migration, same as
    # 6f9bce3448b8's note; only the new table below is intentional.
    op.create_table('supported_countries',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('code', sa.String(length=2), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_supported_countries_code'), 'supported_countries', ['code'], unique=True)

    countries_table = sa.table(
        'supported_countries',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('code', sa.String),
        sa.column('name', sa.String),
        sa.column('sort_order', sa.Integer),
    )
    op.bulk_insert(
        countries_table,
        [
            {"id": str(uuid.uuid4()), "code": code, "name": name, "sort_order": sort_order}
            for code, name, sort_order in _SEED_COUNTRIES
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_supported_countries_code'), table_name='supported_countries')
    op.drop_table('supported_countries')
