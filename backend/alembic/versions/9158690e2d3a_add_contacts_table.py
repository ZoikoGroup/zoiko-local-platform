"""add contacts table

Revision ID: 9158690e2d3a
Revises: 5087dcde5a06
Create Date: 2026-08-05 17:16:39.681277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9158690e2d3a'
down_revision: Union[str, None] = '5087dcde5a06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: this chain's contacts table is the same one 9d5f5c042e44
    # already created on the parallel branch, merged in by a later revision.
    # That version is the one the merged app/contacts/models.py matches
    # (adds created_by_user_id, and deliberately does NOT add a
    # uq_contacts_account_phone unique constraint - see that model's
    # docstring on why phone_number is intentionally non-unique). Applying
    # this migration's original create_table would both collide with the
    # existing table and reintroduce a constraint the merged model rejects.
    pass


def downgrade() -> None:
    pass
