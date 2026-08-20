"""add numbers.manage_caller_identity capability grant

Revision ID: 697a995390f1
Revises: 5569ce743b30
Create Date: 2026-08-19 00:00:03.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '697a995390f1'
down_revision: Union[str, None] = '5569ce743b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Same fraud/abuse-response bar as risk's fraud-case-resolve capability
# (SUPER_ADMIN + COMPLIANCE_OFFICER) - revoking a caller identity is a
# spoofing-complaint response, not routine number administration.
_GRANTS: list[tuple[str, list[str]]] = [
    ("numbers.manage_caller_identity", ["SUPER_ADMIN", "COMPLIANCE_OFFICER"]),
]


def upgrade() -> None:
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [
            {"id": str(uuid.uuid4()), "capability": capability, "role": role}
            for capability, roles in _GRANTS
            for role in roles
        ],
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'numbers.manage_caller_identity'")
