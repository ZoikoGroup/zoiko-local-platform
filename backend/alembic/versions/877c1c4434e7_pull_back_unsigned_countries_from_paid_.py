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

Uses the real service function (not a raw UPDATE) so this goes through the
exact same audit-logging and cache-invalidation path as any other market
status change - not a shortcut around it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session


# revision identifiers, used by Alembic.
revision: str = '877c1c4434e7'
down_revision: Union[str, None] = '679061a79e97'
branch_labels: Union[str, Sequence[str], None] = None
# Real gap fix: this migration calls set_market_activation_status (the live
# app.numbering.numbers.service function), whose SupportedCountry query
# selects every column on the CURRENT ORM model - including
# customer_type_restrictions, added by 5c748e686bbf on an entirely
# separate branch with no ancestry relationship to this one. With no
# explicit dependency, Alembic's topological sort is free to order this
# migration BEFORE 5c748e686bbf, and confirmed live that replaying the
# full chain from an empty database in one continuous run does exactly
# that - "column supported_countries.customer_type_restrictions does not
# exist". depends_on is Alembic's own documented mechanism for a
# cross-branch ordering requirement like this, independent of down_revision
# ancestry.
depends_on: Union[str, Sequence[str], None] = ('5c748e686bbf',)

_REASON = (
    "Pulled back from PAID_OPEN: no legal/tax/telecom/privacy review was ever recorded for this "
    "market (Readiness Standard doc §6.2) - reopen only after a real named review, via the same "
    "legal_signoff_reference/legal_signoff_by gate every other PAID_OPEN transition requires."
)


def upgrade() -> None:
    from app.numbering.numbers.models import MarketActivationStatus
    from app.numbering.numbers.service import set_market_activation_status

    bind = op.get_bind()
    session = Session(bind=bind)
    try:
        codes = [
            row[0]
            for row in session.execute(
                sa.text(
                    "SELECT code FROM supported_countries "
                    "WHERE market_status = 'PAID_OPEN' AND legal_signoff_reference IS NULL"
                )
            ).fetchall()
        ]
        for code in codes:
            set_market_activation_status(
                session, code, status=MarketActivationStatus.CONTROLLED_BETA,
                actor="migration:877c1c4434e7", reason=_REASON,
            )
    finally:
        session.close()


def downgrade() -> None:
    # Deliberately NOT restoring PAID_OPEN - that would silently recreate
    # the exact unsigned-market state this migration exists to fix. A real
    # re-open requires a real review, run through the normal staff route,
    # not a migration rollback.
    pass
