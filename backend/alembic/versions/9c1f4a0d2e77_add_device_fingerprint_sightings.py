"""add device_fingerprint_sightings and device_fingerprint_abuse signal

Revision ID: 9c1f4a0d2e77
Revises: 7a2e5c918bf4
Create Date: 2026-08-10 13:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '9c1f4a0d2e77'
down_revision: Union[str, None] = '7a2e5c918bf4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TYPE risksignaltype ADD VALUE IF NOT EXISTS 'DEVICE_FINGERPRINT_ABUSE'")

    op.create_table(
        'device_fingerprint_sightings',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('fingerprint_hash', sa.String(length=64), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_device_fingerprint_sightings_fingerprint_hash'),
        'device_fingerprint_sightings', ['fingerprint_hash'], unique=False,
    )
    op.create_index(
        op.f('ix_device_fingerprint_sightings_account_id'),
        'device_fingerprint_sightings', ['account_id'], unique=False,
    )
    op.create_index(
        op.f('ix_device_fingerprint_sightings_created_at'),
        'device_fingerprint_sightings', ['created_at'], unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_device_fingerprint_sightings_created_at'), table_name='device_fingerprint_sightings')
    op.drop_index(op.f('ix_device_fingerprint_sightings_account_id'), table_name='device_fingerprint_sightings')
    op.drop_index(op.f('ix_device_fingerprint_sightings_fingerprint_hash'), table_name='device_fingerprint_sightings')
    op.drop_table('device_fingerprint_sightings')
    # Enum VALUE removal isn't supported by Postgres - DEVICE_FINGERPRINT_ABUSE
    # stays defined even on downgrade, same tradeoff as SPEND_LIMIT_EXCEEDED.
