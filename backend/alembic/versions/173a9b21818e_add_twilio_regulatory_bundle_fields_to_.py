"""add twilio regulatory bundle fields to number eligibility case

Revision ID: 173a9b21818e
Revises: ff2c4f882f7c
Create Date: 2026-08-22 18:07:05.869345

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '173a9b21818e'
down_revision: Union[str, None] = 'ff2c4f882f7c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'number_eligibility_cases',
        sa.Column('documents', sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )
    op.alter_column('number_eligibility_cases', 'documents', server_default=None)
    op.add_column('number_eligibility_cases', sa.Column('twilio_end_user_sid', sa.String(length=50), nullable=True))
    op.add_column('number_eligibility_cases', sa.Column('twilio_supporting_document_sid', sa.String(length=50), nullable=True))
    op.add_column('number_eligibility_cases', sa.Column('twilio_bundle_sid', sa.String(length=50), nullable=True))
    op.add_column('number_eligibility_cases', sa.Column('twilio_bundle_status', sa.String(length=30), nullable=True))
    op.add_column('number_eligibility_cases', sa.Column('twilio_bundle_rejection_reason', sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column('number_eligibility_cases', 'twilio_bundle_rejection_reason')
    op.drop_column('number_eligibility_cases', 'twilio_bundle_status')
    op.drop_column('number_eligibility_cases', 'twilio_bundle_sid')
    op.drop_column('number_eligibility_cases', 'twilio_supporting_document_sid')
    op.drop_column('number_eligibility_cases', 'twilio_end_user_sid')
    op.drop_column('number_eligibility_cases', 'documents')
