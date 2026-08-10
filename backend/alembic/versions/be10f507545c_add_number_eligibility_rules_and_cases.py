"""add number_type to phone_numbers, number eligibility rules and cases

Revision ID: be10f507545c
Revises: d05ac876b3ab
Create Date: 2026-08-10 17:15:00.000000

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'be10f507545c'
down_revision: Union[str, None] = 'd05ac876b3ab'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §7 "Number Inventory,
    # Eligibility, Reservation & Provisioning" - formalizes the number
    # lifecycle's market/number-type-specific eligibility_case concept.
    # server_default backfills every existing row to "local" (the only
    # type this platform sells end-to-end today), so this is a no-op for
    # current data.
    op.add_column(
        'phone_numbers',
        sa.Column('number_type', sa.String(length=30), nullable=False, server_default='local'),
    )

    op.create_table(
        'number_eligibility_rules',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('number_type', sa.String(length=30), nullable=False),
        sa.Column('required_evidence', sa.JSON(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('country', 'number_type', name='uq_number_eligibility_rule_country_type'),
    )
    op.create_index(op.f('ix_number_eligibility_rules_country'), 'number_eligibility_rules', ['country'], unique=False)
    op.create_index(op.f('ix_number_eligibility_rules_number_type'), 'number_eligibility_rules', ['number_type'], unique=False)

    op.create_table(
        'number_eligibility_cases',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('phone_number_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('account_id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column('country', sa.String(length=2), nullable=False),
        sa.Column('number_type', sa.String(length=30), nullable=False),
        sa.Column(
            'status',
            sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', name='number_eligibility_case_status_enum'),
            nullable=False,
        ),
        sa.Column('evidence', sa.JSON(), nullable=False),
        sa.Column('review_notes', sa.String(length=500), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['phone_number_id'], ['phone_numbers.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_number_eligibility_cases_phone_number_id'), 'number_eligibility_cases', ['phone_number_id'], unique=False)
    op.create_index(op.f('ix_number_eligibility_cases_account_id'), 'number_eligibility_cases', ['account_id'], unique=False)

    # RBAC capability for PUT/DELETE /staff/number-eligibility-rules (see
    # 8e3f1a5d92c7/d05ac876b3ab for the same seed-a-new-capability pattern).
    # Case approve/reject reuses the existing compliance.review_case grant
    # (same reviewers, same trust bar as KYC/KYB case review).
    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', postgresql.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [{"id": str(uuid.uuid4()), "capability": "numbers.manage_eligibility_rules", "role": "SUPER_ADMIN"}],
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'numbers.manage_eligibility_rules'")
    op.drop_index(op.f('ix_number_eligibility_cases_account_id'), table_name='number_eligibility_cases')
    op.drop_index(op.f('ix_number_eligibility_cases_phone_number_id'), table_name='number_eligibility_cases')
    op.drop_table('number_eligibility_cases')
    op.execute("DROP TYPE IF EXISTS number_eligibility_case_status_enum")
    op.drop_index(op.f('ix_number_eligibility_rules_number_type'), table_name='number_eligibility_rules')
    op.drop_index(op.f('ix_number_eligibility_rules_country'), table_name='number_eligibility_rules')
    op.drop_table('number_eligibility_rules')
    op.drop_column('phone_numbers', 'number_type')
