"""ZL-COM-LAUNCH-001: country/capability launch-control schema

Revision ID: b1c4e8a9d3f7
Revises: c8e3f56a1b09
Create Date: 2026-09-11 00:00:00.000000

Executive directive ZL-COM-LAUNCH-001 (2026-09-11) requires a 5-stage
country status (CLOSED/LAUNCHING/OPEN/RESTRICTED/SUSPENDED, replacing the
4-stage model this table used before), per-capability flags per country
(not one COUNTRY_OPEN boolean), and three separately-recorded approvals
(Regulatory, Finance, Commercial) per country. This migration adds the
schema; the follow-up data migration moves existing rows into it per the
directive's own §6 instruction ("place all 16 countries into LAUNCHING
immediately").

Same autocommit_block() requirement as 9f1c6d4a2b83 - Postgres refuses to
use a freshly added enum value inside the transaction that added it.
Enum labels are the Python enum MEMBER NAMES (uppercase), matching this
column's existing labels (CLOSED, INTERNAL_TEST, CONTROLLED_BETA,
PAID_OPEN, SUSPENDED are already uppercase for the same
values_callable-less Enum(str, ...) reason as every other enum in this
codebase).

Postgres can't rename or remove enum labels - INTERNAL_TEST/
CONTROLLED_BETA/PAID_OPEN stay defined on market_activation_status_enum
forever, unused going forward (the data migration moves every row off
them).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = 'b1c4e8a9d3f7'
down_revision: Union[str, None] = 'c8e3f56a1b09'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE market_activation_status_enum ADD VALUE IF NOT EXISTS 'LAUNCHING'")
        op.execute("ALTER TYPE market_activation_status_enum ADD VALUE IF NOT EXISTS 'OPEN'")
        op.execute("ALTER TYPE market_activation_status_enum ADD VALUE IF NOT EXISTS 'RESTRICTED'")
        op.execute("ALTER TYPE platform_staff_role_enum ADD VALUE IF NOT EXISTS 'FINANCE_OFFICER'")

    op.add_column('supported_countries', sa.Column('customer_signup_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('supported_countries', sa.Column('number_search_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('supported_countries', sa.Column('number_purchase_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('supported_countries', sa.Column('inbound_voice_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('supported_countries', sa.Column('outbound_voice_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('supported_countries', sa.Column('sms_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('supported_countries', sa.Column('recording_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))

    # Not created explicitly here - create_table below auto-creates both
    # enum types as it creates the table (SQLAlchemy's default behavior
    # for an inline sa.Enum(...) column type); calling .create() first
    # AND create_table would attempt to create each type twice.
    country_approval_type_enum = sa.Enum('REGULATORY', 'FINANCE', 'COMMERCIAL', name='country_approval_type_enum')
    country_approval_status_enum = sa.Enum('PENDING', 'APPROVED', 'REJECTED', name='country_approval_status_enum')

    op.create_table(
        'country_approval_records',
        sa.Column('id', UUID(as_uuid=False), primary_key=True),
        sa.Column('country_id', UUID(as_uuid=False), sa.ForeignKey('supported_countries.id'), nullable=False),
        sa.Column('approval_type', country_approval_type_enum, nullable=False),
        sa.Column('status', country_approval_status_enum, nullable=False, server_default='PENDING'),
        sa.Column('owner_name', sa.String(150), nullable=True),
        sa.Column('owner_title', sa.String(150), nullable=True),
        sa.Column('evidence_reference', sa.String(200), nullable=True),
        sa.Column('reason', sa.String(2000), nullable=True),
        sa.Column('decided_by', UUID(as_uuid=False), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('country_id', 'approval_type', name='uq_country_approval_type'),
    )

    # New capabilities for the 3-way approval split (ZL-COM-LAUNCH-001 §5) -
    # numbers.manage_country_list (SUPER_ADMIN only, pre-existing) keeps
    # gating capability-flag toggles and row management, but no longer
    # single-handedly gates the OPEN transition - see the service-layer
    # change in the next migration/commit for the enforcement side.
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.Enum(name='platform_staff_role_enum')),
    )
    import uuid
    op.bulk_insert(grants_table, [
        {'id': str(uuid.uuid4()), 'capability': 'numbers.approve_country_regulatory', 'role': 'COMPLIANCE_OFFICER'},
        {'id': str(uuid.uuid4()), 'capability': 'numbers.approve_country_regulatory', 'role': 'SUPER_ADMIN'},
        {'id': str(uuid.uuid4()), 'capability': 'numbers.approve_country_finance', 'role': 'FINANCE_OFFICER'},
        {'id': str(uuid.uuid4()), 'capability': 'numbers.approve_country_finance', 'role': 'SUPER_ADMIN'},
        {'id': str(uuid.uuid4()), 'capability': 'numbers.approve_country_commercial', 'role': 'SUPER_ADMIN'},
    ])


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability IN "
               "('numbers.approve_country_regulatory', 'numbers.approve_country_finance', 'numbers.approve_country_commercial')")
    op.drop_table('country_approval_records')
    sa.Enum(name='country_approval_status_enum').drop(op.get_bind())
    sa.Enum(name='country_approval_type_enum').drop(op.get_bind())
    op.drop_column('supported_countries', 'recording_enabled')
    op.drop_column('supported_countries', 'sms_enabled')
    op.drop_column('supported_countries', 'outbound_voice_enabled')
    op.drop_column('supported_countries', 'inbound_voice_enabled')
    op.drop_column('supported_countries', 'number_purchase_enabled')
    op.drop_column('supported_countries', 'number_search_enabled')
    op.drop_column('supported_countries', 'customer_signup_enabled')
    # Enum VALUE removal isn't supported by Postgres - LAUNCHING/OPEN/
    # RESTRICTED/FINANCE_OFFICER stay defined even on downgrade.
