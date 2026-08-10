"""add synthetic check runs

Revision ID: 46b8d21987b5
Revises: a7c3e9f1d5b8
Create Date: 2026-08-07 11:10:16.571360

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '46b8d21987b5'
down_revision: Union[str, None] = 'a7c3e9f1d5b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('synthetic_check_runs',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('check_name', sa.String(length=100), nullable=False),
    sa.Column('success', sa.Boolean(), nullable=False),
    sa.Column('duration_ms', sa.Float(), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_synthetic_check_runs_check_name'), 'synthetic_check_runs', ['check_name'], unique=False)
    op.create_index(op.f('ix_synthetic_check_runs_created_at'), 'synthetic_check_runs', ['created_at'], unique=False)
    op.create_index(op.f('ix_synthetic_check_runs_success'), 'synthetic_check_runs', ['success'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_synthetic_check_runs_success'), table_name='synthetic_check_runs')
    op.drop_index(op.f('ix_synthetic_check_runs_created_at'), table_name='synthetic_check_runs')
    op.drop_index(op.f('ix_synthetic_check_runs_check_name'), table_name='synthetic_check_runs')
    op.drop_table('synthetic_check_runs')
