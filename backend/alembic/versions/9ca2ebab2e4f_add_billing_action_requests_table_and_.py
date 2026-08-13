"""add billing_action_requests table and approve_billing_action capability grant

Revision ID: 9ca2ebab2e4f
Revises: 2bba6f0a10a3
Create Date: 2026-08-12 14:57:13.327034

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '9ca2ebab2e4f'
down_revision: Union[str, None] = '2bba6f0a10a3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc §26 "Approver... cannot
    # self-approve where policy applies" - maker-checker for the 4
    # highest-risk money-moving ZoikoNex actions (see
    # app.billing.models.BillingActionRequest's docstring). requested_by/
    # approved_by are plain strings (staff.id), matching this codebase's
    # existing "actor" convention elsewhere (e.g.
    # ZoikoNexReconciliationException.resolved_by) rather than a hard FK.
    op.create_table(
        'billing_action_requests',
        sa.Column('id', postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column(
            'action_type',
            sa.Enum('CREDIT_NOTE', 'DEBIT_NOTE', 'REFUND', 'RUN_BILLING_CYCLE', name='billing_action_type_enum'),
            nullable=False,
        ),
        sa.Column('payload', postgresql.JSONB(), nullable=False),
        sa.Column('requested_by', sa.String(length=100), nullable=False),
        sa.Column(
            'status',
            sa.Enum('PENDING', 'APPROVED', 'REJECTED', 'EXECUTED', name='billing_action_request_status_enum'),
            nullable=False,
        ),
        sa.Column('approved_by', sa.String(length=100), nullable=True),
        sa.Column('rejection_reason', sa.String(length=255), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_billing_action_requests_action_type'), 'billing_action_requests', ['action_type'], unique=False)
    op.create_index(op.f('ix_billing_action_requests_requested_by'), 'billing_action_requests', ['requested_by'], unique=False)
    op.create_index(op.f('ix_billing_action_requests_status'), 'billing_action_requests', ['status'], unique=False)
    op.create_index(op.f('ix_billing_action_requests_created_at'), 'billing_action_requests', ['created_at'], unique=False)

    # Guarded with a NOT EXISTS check (see d05ac876b3ab's docstring for why
    # a plain bulk_insert here risks a uq_staff_capability_grant violation
    # on a merged chain).
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
        ).bindparams(id=str(uuid.uuid4()), capability="billing.approve_billing_action", role="SUPER_ADMIN")
    )


def downgrade() -> None:
    op.execute("DELETE FROM staff_capability_grants WHERE capability = 'billing.approve_billing_action'")
    op.drop_index(op.f('ix_billing_action_requests_created_at'), table_name='billing_action_requests')
    op.drop_index(op.f('ix_billing_action_requests_status'), table_name='billing_action_requests')
    op.drop_index(op.f('ix_billing_action_requests_requested_by'), table_name='billing_action_requests')
    op.drop_index(op.f('ix_billing_action_requests_action_type'), table_name='billing_action_requests')
    op.drop_table('billing_action_requests')
    op.execute("DROP TYPE IF EXISTS billing_action_request_status_enum")
    op.execute("DROP TYPE IF EXISTS billing_action_type_enum")
