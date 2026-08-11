"""add carrier-evidence leg to zoikonex reconciliation

Revision ID: 4d8b6f0c1a93
Revises: 9c1f4a0d2e77
Create Date: 2026-08-10 14:05:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '4d8b6f0c1a93'
down_revision: Union[str, None] = '9c1f4a0d2e77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Commercial Billing Operating Standard doc's "three-way reconciliation
    # (Zoiko Local <-> ZoikoNex <-> carrier)" - adds the carrier-evidence
    # leg (CallRecord, Twilio's own status-callback data) to the existing
    # ZoikoNex-ledger reconciliation job. See
    # app.billing.models.ZoikoNexReconciliationRun's docstring.
    op.add_column(
        'zoikonex_reconciliation_runs',
        sa.Column('total_completed_calls', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'zoikonex_reconciliation_runs',
        sa.Column('unmatched_completed_calls', sa.Integer(), nullable=False, server_default='0'),
    )
    op.execute(
        "ALTER TYPE zoikonex_reconciliation_exception_type_enum "
        "ADD VALUE IF NOT EXISTS 'CALL_RECORD_MISSING_USAGE_EVENT'"
    )


def downgrade() -> None:
    op.drop_column('zoikonex_reconciliation_runs', 'unmatched_completed_calls')
    op.drop_column('zoikonex_reconciliation_runs', 'total_completed_calls')
    # Enum VALUE removal isn't supported by Postgres - the value stays
    # defined even on downgrade, same tradeoff as other enum-add migrations
    # in this chain (SPEND_LIMIT_EXCEEDED, DEVICE_FINGERPRINT_ABUSE).
