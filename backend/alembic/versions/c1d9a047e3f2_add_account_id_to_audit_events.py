"""add account_id to audit events

Revision ID: c1d9a047e3f2
Revises: 8073c2599e48
Create Date: 2026-08-06 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'c1d9a047e3f2'
down_revision: Union[str, None] = '8073c2599e48'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Turns the audit trail's customer-facing view from a query-time heuristic
# (list_account_events used to OR together actor-in-user-ids and
# target-in-case/porting-ids on every read) into a real, indexed column
# resolved once at write time (see app.audit.service._resolve_account_id).
# This backfill applies that exact same heuristic to existing rows so
# nothing is lost in the switch - a UUID-format guard is needed because
# `actor` is free text (some rows hold "system", staff ids, etc.) and a
# bad cast to uuid would abort the whole statement.
_UUID_RE = "^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("account_id", UUID(as_uuid=False), nullable=True))
    op.create_index("ix_audit_events_account_id", "audit_events", ["account_id"])

    op.execute(sa.text(f"""
        UPDATE audit_events SET account_id = actor::uuid
        WHERE actor ~ '{_UUID_RE}'
          AND actor::uuid IN (SELECT id FROM accounts)
    """))
    op.execute(sa.text(f"""
        UPDATE audit_events SET account_id = users.account_id
        FROM users
        WHERE audit_events.account_id IS NULL
          AND audit_events.actor ~ '{_UUID_RE}'
          AND audit_events.actor::uuid = users.id
    """))
    op.execute(sa.text("""
        UPDATE audit_events SET account_id = compliance_cases.account_id
        FROM compliance_cases
        WHERE audit_events.account_id IS NULL
          AND audit_events.target = 'compliance_case:' || compliance_cases.id::text
    """))
    op.execute(sa.text("""
        UPDATE audit_events SET account_id = porting_requests.account_id
        FROM porting_requests
        WHERE audit_events.account_id IS NULL
          AND audit_events.target = 'porting_request:' || porting_requests.id::text
    """))


def downgrade() -> None:
    op.drop_index("ix_audit_events_account_id", table_name="audit_events")
    op.drop_column("audit_events", "account_id")
