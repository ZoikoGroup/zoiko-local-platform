"""add staff capability grants matrix

Revision ID: ce2bebedfe43
Revises: 155a3edc4305
Create Date: 2026-08-10 12:50:56.183704

"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'ce2bebedfe43'
down_revision: Union[str, None] = '155a3edc4305'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Commercial Billing Operating Standard doc's "formal RBAC/segregation-of-
# duties matrix" ask - this seed exactly reproduces the role sets every
# require_staff_role(...) call site used to hardcode (see app.core.deps.
# require_capability and app.staff.models.StaffCapabilityGrant's
# docstrings), so behavior is unchanged; only where it's decided (data,
# not code) changes.
_GRANTS: list[tuple[str, list[str]]] = [
    ("billing.simulate_payment_event", ["SUPER_ADMIN"]),
    ("billing.resolve_reconciliation_exception", ["SUPER_ADMIN"]),
    ("billing.manage_calling_rates", ["SUPER_ADMIN"]),
    ("compliance.review_case", ["COMPLIANCE_OFFICER", "SUPER_ADMIN"]),
    ("porting.review_request", ["SUPPORT", "SUPER_ADMIN"]),
    ("risk.manage_blocked_destinations", ["SUPER_ADMIN"]),
    ("risk.reinstate_account", ["SUPER_ADMIN", "COMPLIANCE_OFFICER"]),
    ("risk.resolve_fraud_case", ["SUPER_ADMIN", "COMPLIANCE_OFFICER"]),
    ("numbers.manage_provisioning", ["SUPPORT", "SUPER_ADMIN"]),
    ("numbers.manage_renewal", ["SUPPORT", "SUPER_ADMIN"]),
    ("numbers.manage_country_list", ["SUPER_ADMIN"]),
]


def upgrade() -> None:
    # Note: autogenerate also picked up pre-existing, unrelated drift (an
    # 'agent_presence' unique-constraint name and 'calling_rates'
    # nullability/constraint-naming mismatch) - deliberately left out, same
    # as 6f9bce3448b8's and 155a3edc4305's notes. The role column reuses
    # the platform_staff_role_enum type PlatformStaff.role already created
    # (create_type=False) - autogenerate's plain sa.Enum(...) rendering
    # would otherwise try to CREATE TYPE a second time and fail with
    # "type already exists".
    op.create_table('staff_capability_grants',
    sa.Column('id', sa.UUID(as_uuid=False), nullable=False),
    sa.Column('capability', sa.String(length=100), nullable=False),
    sa.Column(
        'role',
        postgresql.ENUM('SUPPORT', 'COMPLIANCE_OFFICER', 'SUPER_ADMIN', name='platform_staff_role_enum', create_type=False),
        nullable=False,
    ),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('capability', 'role', name='uq_staff_capability_grant')
    )
    op.create_index(op.f('ix_staff_capability_grants_capability'), 'staff_capability_grants', ['capability'], unique=False)

    grants_table = sa.table(
        'staff_capability_grants',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('capability', sa.String),
        sa.column('role', sa.String),
    )
    op.bulk_insert(
        grants_table,
        [
            {"id": str(uuid.uuid4()), "capability": capability, "role": role}
            for capability, roles in _GRANTS
            for role in roles
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_staff_capability_grants_capability'), table_name='staff_capability_grants')
    op.drop_table('staff_capability_grants')
