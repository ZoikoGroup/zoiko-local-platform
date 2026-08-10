"""merge anil's 5 divergent migration heads (pre-existing, unrelated to Phase 3 work)

Revision ID: fb89ad3b818a
Revises: 141799fdba9b, c8e14b6a2f3d, a80b7b11ce8e, 9f3ae057ccb3, 65f0fff3c13f
Create Date: 2026-08-06 19:00:00.000000

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'fb89ad3b818a'
down_revision: Union[str, Sequence[str], None] = (
    '141799fdba9b', 'c8e14b6a2f3d', 'a80b7b11ce8e', '9f3ae057ccb3', '65f0fff3c13f',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
