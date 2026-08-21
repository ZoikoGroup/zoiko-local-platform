"""add caller_identities table (Commercial Billing Operating Standard doc §R6)

Revision ID: 5569ce743b30
Revises: 2c8b9960fe70
Create Date: 2026-08-19 00:00:02.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5569ce743b30'
down_revision: Union[str, None] = '2c8b9960fe70'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Idempotent by design, not just by convention: this project's test
    # suite runs a session-scoped Base.metadata.create_all() against the
    # same live database this migration targets - once CallerIdentity
    # exists as a model (it does, as of this same change), a test run can
    # materialize this exact type/table out-of-band before this migration
    # ever executes. A plain CREATE TYPE/CREATE TABLE would then fail with
    # DuplicateObject - confirmed live, not hypothetical.
    enum_exists = conn.execute(
        sa.text("SELECT 1 FROM pg_type WHERE typname = 'caller_identity_status_enum'")
    ).scalar()
    if not enum_exists:
        postgresql.ENUM(
            'UNVERIFIED', 'VERIFIED', 'RESTRICTED', 'EXPIRED', 'REVOKED',
            name='caller_identity_status_enum',
        ).create(conn)

    table_exists = conn.execute(sa.text("SELECT to_regclass('caller_identities')")).scalar()
    if not table_exists:
        op.create_table(
            'caller_identities',
            sa.Column('id', postgresql.UUID(as_uuid=False), primary_key=True),
            sa.Column('phone_number_id', postgresql.UUID(as_uuid=False), nullable=False, unique=True),
            sa.Column('account_id', postgresql.UUID(as_uuid=False), nullable=False),
            sa.Column(
                'status', postgresql.ENUM(name='caller_identity_status_enum', create_type=False),
                nullable=False, server_default='UNVERIFIED',
            ),
            sa.Column('verification_source', sa.String(length=100), nullable=True),
            sa.Column('scope', sa.String(length=50), nullable=False, server_default='global'),
            sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()')),
            sa.ForeignKeyConstraint(['phone_number_id'], ['phone_numbers.id'], ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ondelete='CASCADE'),
        )
        op.create_index('ix_caller_identities_phone_number_id', 'caller_identities', ['phone_number_id'], unique=True)
        op.create_index('ix_caller_identities_account_id', 'caller_identities', ['account_id'])

    # Backfill: every number ALREADY active today was provisioned through
    # this platform's own purchase/port-in flow before this feature
    # existed - grandfathered in as VERIFIED so the new
    # assert_caller_id_authorized gate (wired into place_outbound_call)
    # doesn't retroactively block calling for numbers nobody re-verifies.
    # New numbers get a real row from purchase_number/complete_porting_
    # request going forward - see _auto_verify_caller_identity. Guarded on
    # an empty table so a re-run (e.g. after the table already existed
    # from the checkfirst branch above) never double-inserts.
    already_backfilled = conn.execute(sa.text("SELECT 1 FROM caller_identities LIMIT 1")).scalar()
    if not already_backfilled:
        op.execute("""
            INSERT INTO caller_identities (id, phone_number_id, account_id, status, verification_source, scope, verified_at, created_at)
            SELECT gen_random_uuid(), id, account_id, 'VERIFIED', 'backfilled_existing_active_number', 'global', now(), now()
            FROM phone_numbers
            WHERE status IN ('ACTIVE', 'SUSPENDED') AND account_id IS NOT NULL
        """)


def downgrade() -> None:
    op.drop_index('ix_caller_identities_account_id', table_name='caller_identities')
    op.drop_index('ix_caller_identities_phone_number_id', table_name='caller_identities')
    op.drop_table('caller_identities')
    postgresql.ENUM(name='caller_identity_status_enum').drop(op.get_bind())
