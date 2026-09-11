"""ZL-COM-LAUNCH-001: move all countries into LAUNCHING, record Commercial approval

Revision ID: d4a7f2c6b8e1
Revises: b1c4e8a9d3f7
Create Date: 2026-09-11 00:00:01.000000

Data migration implementing ZL-COM-LAUNCH-001 §6's explicit instruction:
"Place all 16 countries into LAUNCHING immediately; remove the former Wave
sequencing dependency." Every existing SupportedCountry row (whether
previously CLOSED or CONTROLLED_BETA) moves to LAUNCHING - a safe holding
state (nothing customer-facing works from LAUNCHING alone; see
assert_country_capability), not a public opening.

Also records the COMMERCIAL approval the directive itself constitutes -
the document is signed by Lennox G. McLeod (Founder & Executive Chairman,
Zoiko Group) with a canonical approval reason (§5), dated today. This is
a REAL recorded approval, not a placeholder - it's the actual directive.
The REGULATORY and FINANCE approval rows are inserted as genuine PENDING
placeholders (no owner, no decision) since neither review has happened
yet - backfilling a fake reviewer name would be exactly the kind of
invented approval this directive prohibits ("executive approval must not
impersonate specialist clearance").

All 7 new capability flags stay at their column default (False) for every
row - nothing is enabled by this migration. Each capability is turned on
individually by staff once its own review clears (see the new
POST /staff/countries/{code}/capabilities route).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4a7f2c6b8e1'
down_revision: Union[str, None] = 'b1c4e8a9d3f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_COMMERCIAL_REASON = (
    "Executive commercial approval for simultaneous Zoiko Local launch across the 16 "
    "approved markets under the country- and service-level launch-control framework. "
    "Mandatory legal, tax, carrier and safety blockers are non-waivable. Any residual "
    "risk accepted for launch is documented with owner, mitigation and review date."
)


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text(
        "UPDATE supported_countries SET market_status = 'LAUNCHING' "
        "WHERE market_status IN ('CLOSED', 'INTERNAL_TEST', 'CONTROLLED_BETA', 'PAID_OPEN')"
    ))

    country_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM supported_countries")).fetchall()]

    approval_table = sa.table(
        'country_approval_records',
        sa.column('id', sa.String),
        sa.column('country_id', sa.String),
        sa.column('approval_type', sa.String),
        sa.column('status', sa.String),
        sa.column('owner_name', sa.String),
        sa.column('owner_title', sa.String),
        sa.column('evidence_reference', sa.String),
        sa.column('reason', sa.String),
        sa.column('decided_at', sa.DateTime),
    )

    import uuid
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    rows = []
    for country_id in country_ids:
        rows.append({
            'id': str(uuid.uuid4()), 'country_id': country_id, 'approval_type': 'COMMERCIAL',
            'status': 'APPROVED', 'owner_name': 'Lennox G. McLeod',
            'owner_title': 'Founder & Executive Chairman, Zoiko Group',
            'evidence_reference': 'ZL-COM-LAUNCH-001', 'reason': _COMMERCIAL_REASON, 'decided_at': now,
        })
        rows.append({
            'id': str(uuid.uuid4()), 'country_id': country_id, 'approval_type': 'REGULATORY',
            'status': 'PENDING', 'owner_name': None, 'owner_title': None,
            'evidence_reference': None, 'reason': None, 'decided_at': None,
        })
        rows.append({
            'id': str(uuid.uuid4()), 'country_id': country_id, 'approval_type': 'FINANCE',
            'status': 'PENDING', 'owner_name': None, 'owner_title': None,
            'evidence_reference': None, 'reason': None, 'decided_at': None,
        })

    if rows:
        op.bulk_insert(approval_table, rows)


def downgrade() -> None:
    op.execute("DELETE FROM country_approval_records")
    # Data-only reversal - status left at LAUNCHING rather than guessing
    # back to each row's prior CLOSED/CONTROLLED_BETA value, since that
    # mapping isn't recoverable after the fact.
