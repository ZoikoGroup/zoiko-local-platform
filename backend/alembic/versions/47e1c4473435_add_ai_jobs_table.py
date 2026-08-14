"""add ai_jobs table: retry lineage + failure observability

Revision ID: 47e1c4473435
Revises: bcddcc835028
Create Date: 2026-08-13 20:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '47e1c4473435'
down_revision: Union[str, None] = 'bcddcc835028'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No explicit .create() here - op.create_table below creates a new
    # sa.Enum type automatically (create_type defaults to True); calling
    # .create() first AND passing the same object to create_table double-
    # creates it (confirmed live: "type already exists" on a DB where the
    # type provably did not exist beforehand).
    ai_job_status_enum = sa.Enum('RUNNING', 'SUCCEEDED', 'FAILED', name='ai_job_status_enum')

    # summary_source_type_enum already exists (conversation_summaries'
    # original migration) - reused via create_type=False, not recreated.
    summary_source_type_enum = postgresql.ENUM(
        'VOICEMAIL', 'CALL', 'VIDEO', name='summary_source_type_enum', create_type=False,
    )

    op.create_table(
        'ai_jobs',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('source_type', summary_source_type_enum, nullable=False),
        sa.Column('source_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('status', ai_job_status_enum, nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('conversation_summary_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['conversation_summary_id'], ['conversation_summaries.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('source_type', 'source_id', name='uq_ai_job_source'),
    )
    op.create_index(op.f('ix_ai_jobs_account_id'), 'ai_jobs', ['account_id'], unique=False)
    op.create_index(op.f('ix_ai_jobs_source_id'), 'ai_jobs', ['source_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ai_jobs_source_id'), table_name='ai_jobs')
    op.drop_index(op.f('ix_ai_jobs_account_id'), table_name='ai_jobs')
    op.drop_table('ai_jobs')
    op.execute("DROP TYPE IF EXISTS ai_job_status_enum")
