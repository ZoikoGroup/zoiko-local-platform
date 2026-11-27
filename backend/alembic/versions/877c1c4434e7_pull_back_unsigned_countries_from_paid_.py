"""pull back unsigned countries from paid open pending real legal review

Revision ID: 877c1c4434e7
Revises: 679061a79e97
Create Date: 2026-08-19 11:32:59.442527

Readiness Standard doc §6.2 "Market Activation Registry" - "PAID_OPEN only
after legal/tax/telecom/privacy/consumer review and named sign-off." All 8
seeded countries (US/CA/GB/AU/DE/FR/IN/SG) reached PAID_OPEN via an old
migration's raw UPDATE (predating legal_signoff_reference/legal_signoff_by,
added in 4ec152435b05) - real customers could buy real numbers in all 8
markets with zero recorded review on file.

Explicit product decision (2026-08-19, confirmed via user prompt, not
engineering's own call): pull all 8 back to CONTROLLED_BETA rather than
fabricate signoff evidence for a review that may never have actually
happened. CONTROLLED_BETA still lets is_test accounts through (see
_assert_market_activated's docstring) so internal testing is unaffected;
real customer purchases in these markets stop until someone with real
legal/tax authority reviews each one and reopens it via PUT
/staff/countries/{code}/market-status with real legal_signoff_reference/
legal_signoff_by - the same gate every future PAID_OPEN transition already
goes through.

Originally this called the live set_market_activation_status service function
for audit-logging parity. That is not replay-safe: the service queries the
CURRENT SupportedCountry ORM model (SELECT of every column), so replaying the
chain from an empty database failed on columns added by later revisions
(customer_type_restrictions and the per-capability flags). Alembic's
depends_on was added to force the column ahead of this revision, but that
branch has since been merged into this revision's own ancestry, making the
dependency circular - _topological_sort ping-pongs forever and every
`alembic upgrade head` from empty hangs. This now does the same UPDATE and
audit row directly, touching only columns that exist at this point in the
chain; the audit entry keeps the same actor/action/target/reason and
before/after state hashes log_event would have written.
"""
import hashlib
import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '877c1c4434e7'
down_revision: Union[str, None] = '679061a79e97'
branch_labels: Union[str, Sequence[str], None] = None
# No cross-branch ordering requirement anymore. 5c748e686bbf (the migration
# that adds supported_countries.customer_type_restrictions) is now a
# DESCENDANT of this revision in the merged down_revision chain, so the old
# depends_on=('5c748e686bbf',) asked for a revision to run before its own
# ancestor - Alembic's topological sort never terminates (infinite hang on
# every fresh `alembic upgrade head`). upgrade() below no longer reads the
# live ORM, so it doesn't need those later columns at all.
depends_on: Union[str, Sequence[str], None] = None

_REASON = (
    "Pulled back from PAID_OPEN: no legal/tax/telecom/privacy review was ever recorded for this "
    "market (Readiness Standard doc §6.2) - reopen only after a real named review, via the same "
    "legal_signoff_reference/legal_signoff_by gate every other PAID_OPEN transition requires."
)


def _state_hash(state: dict) -> str:
    canonical = json.dumps(state, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    bind = op.get_bind()
    codes = [
        row[0]
        for row in bind.execute(
            sa.text(
                "UPDATE supported_countries SET market_status = 'CONTROLLED_BETA' "
                "WHERE market_status = 'PAID_OPEN' AND legal_signoff_reference IS NULL "
                "RETURNING code"
            )
        ).fetchall()
    ]
    before_hash = _state_hash({"market_status": "PAID_OPEN"})
    after_hash = _state_hash({"market_status": "CONTROLLED_BETA"})
    for code in codes:
        bind.execute(
            sa.text(
                "INSERT INTO audit_events "
                "(id, actor, action, target, reason, before_hash, after_hash, created_at) "
                "VALUES (:id, :actor, :action, :target, :reason, :before_hash, :after_hash, now())"
            ),
            {
                "id": str(uuid.uuid4()),
                "actor": "migration:877c1c4434e7",
                "action": "market.activation_status_changed",
                "target": f"supported_country:{code}",
                "reason": _REASON,
                "before_hash": before_hash,
                "after_hash": after_hash,
            },
        )


def downgrade() -> None:
    # Deliberately NOT restoring PAID_OPEN - that would silently recreate
    # the exact unsigned-market state this migration exists to fix. A real
    # re-open requires a real review, run through the normal staff route,
    # not a migration rollback.
    pass
