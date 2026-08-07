"""add call_flows and call_flow_versions (Advanced IVR builder, Phase 3)

Originally authored against venky's history (Revises: b292ad2dad84, the
ring_group_destinations migration) - retargeted onto fb89ad3b818a when this
work was cherry-picked onto the anil branch, which never had that
migration (ring groups are a venky-only feature). No SQL in this file
changed, only its place in the graph.

Revision ID: 4b2d27efe669
Revises: fb89ad3b818a
Create Date: 2026-08-06 17:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '4b2d27efe669'
down_revision: Union[str, None] = 'fb89ad3b818a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'call_flows',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_call_flows_account_id'), 'call_flows', ['account_id'], unique=False)

    # Labels are uppercase to match this codebase's existing convention of
    # storing a Python (str, Enum)'s *member name* in Postgres (see
    # phone_number_status_enum's 'RESERVED'/'ACTIVE'/... labels) - SQLAlchemy
    # binds Enum(SomePyEnum) columns by .name, not .value, by default.
    call_flow_version_status_enum = postgresql.ENUM('DRAFT', 'PUBLISHED', 'ARCHIVED', name='call_flow_version_status_enum', create_type=False)
    call_flow_version_status_enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        'call_flow_versions',
        sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('call_flow_id', sa.UUID(as_uuid=False), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('status', call_flow_version_status_enum, nullable=False),
        sa.Column('entry_node_id', sa.String(length=100), nullable=False),
        sa.Column('nodes', sa.JSON(), nullable=False),
        sa.Column('created_by_user_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('published_by_user_id', sa.UUID(as_uuid=False), nullable=True),
        sa.Column('rolled_back_from_version', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['call_flow_id'], ['call_flows.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['published_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_call_flow_versions_call_flow_id'), 'call_flow_versions', ['call_flow_id'], unique=False)

    op.add_column('phone_numbers', sa.Column('call_flow_id', sa.UUID(as_uuid=False), nullable=True))
    op.create_index(op.f('ix_phone_numbers_call_flow_id'), 'phone_numbers', ['call_flow_id'], unique=False)
    op.create_foreign_key(
        'fk_phone_numbers_call_flow_id', 'phone_numbers', 'call_flows', ['call_flow_id'], ['id'], ondelete='SET NULL'
    )


def downgrade() -> None:
    op.drop_constraint('fk_phone_numbers_call_flow_id', 'phone_numbers', type_='foreignkey')
    op.drop_index(op.f('ix_phone_numbers_call_flow_id'), table_name='phone_numbers')
    op.drop_column('phone_numbers', 'call_flow_id')

    op.drop_index(op.f('ix_call_flow_versions_call_flow_id'), table_name='call_flow_versions')
    op.drop_table('call_flow_versions')
    postgresql.ENUM(name='call_flow_version_status_enum', create_type=False).drop(op.get_bind(), checkfirst=True)

    op.drop_index(op.f('ix_call_flows_account_id'), table_name='call_flows')
    op.drop_table('call_flows')
