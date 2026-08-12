"""add authorization_evidence_url and target_completion_date to porting_requests

Revision ID: 2bba6f0a10a3
Revises: f606e9a82525
Create Date: 2026-08-12 14:27:03.508224

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2bba6f0a10a3'
down_revision: Union[str, None] = 'f606e9a82525'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §I1 - authorization
    # evidence (an object-storage reference, see app.porting.models.
    # PortingRequest's docstring) and a customer-requested target
    # completion date. Both nullable/optional - port-in here remains a
    # staff-mediated manual hand-off, not blocked on either field.
    op.add_column('porting_requests', sa.Column('authorization_evidence_url', sa.String(length=500), nullable=True))
    op.add_column('porting_requests', sa.Column('target_completion_date', sa.Date(), nullable=True))


def downgrade() -> None:
    op.drop_column('porting_requests', 'target_completion_date')
    op.drop_column('porting_requests', 'authorization_evidence_url')
