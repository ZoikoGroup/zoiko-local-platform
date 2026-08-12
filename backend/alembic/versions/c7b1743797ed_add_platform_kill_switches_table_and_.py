"""add platform_kill_switches table and manage_kill_switches capability grant

Revision ID: c7b1743797ed
Revises: 97bdd2633140
Create Date: 2026-08-12 16:12:33.694306

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c7b1743797ed'
down_revision: Union[str, None] = '97bdd2633140'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §32.1 - platform-wide,
    # manually-triggered kill switches (see app.ops.models.
    # PlatformKillSwitch's docstring). One upserted row per scope.
    op.create_table(
        'platform_kill_switches',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            'scope',
            sa.Enum(
                'NUMBER_PROVISIONING', 'OUTBOUND_CALLING', 'AI_PROCESSING', 'PAYMENTS_BILLING',
                name='kill_switch_scope_enum',
            ),
            nullable=False,
        ),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('activated_by', sa.String(length=100), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deactivated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_platform_kill_switches_scope'), 'platform_kill_switches', ['scope'], unique=True)

    op.execute(
        sa.text(
            """
            INSERT INTO staff_capability_grants (id, capability, role)
            SELECT :id, :capability, :role
            WHERE NOT EXISTS (
                SELECT 1 FROM staff_capability_grants
                WHERE capability = :capability AND role = :role
            )
            """
        ).bindparams(id=str(uuid.uuid4()), capability="ops.manage_kill_switches", role="SUPER_ADMIN")
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'ops.manage_kill_switches'")
    op.drop_index(op.f('ix_platform_kill_switches_scope'), table_name='platform_kill_switches')
    op.drop_table('platform_kill_switches')
    op.execute("DROP TYPE IF EXISTS kill_switch_scope_enum")
