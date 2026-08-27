"""seed phase 2 entitlement keys: team members, business hours, shared handling, reporting

Revision ID: 245b5ab62c15
Revises: 710c98f42bfc
Create Date: 2026-08-24 15:25:24.631564

ZL-COM-ENT-001 §7 matrix, Phase 2 of the entitlement rollout - extends the
same `plan_entitlements` table Phase 1 (710c98f42bfc) introduced with the
next batch of keys.

Three are actively enforced (see app.numbering.identity.service.
add_team_member, app.numbering.numbers.service.configure_routing/
set_ring_group):
  - team.members.enabled: Starter previously had no gate on the team
    feature at all beyond its numeric Plan.max_team_seats cap (5 seats) -
    the doc's matrix says Starter should have NO team-member capability,
    period.
  - routing.business_hours: configuring business hours at all, not just
    the existing business_hours_timezone default.
  - routing.shared_handling: a 2+-destination ring group (a single
    destination is just personal forwarding, unaffected).

Three are seeded for catalog completeness per the doc's Appendix A but
NOT wired to a route check yet, same "no natural single code hook"
posture Phase 1 already applied to admin.multi_number/support.priority:
  - routing.team: no feature in this codebase is distinctly "team
    routing" separate from business-hours routing and ring groups.
  - reporting.business: no reporting feature in this codebase sits
    between reporting.usage (granted to everyone) and reporting.advanced
    (the Pro+ analytics module already gated in Phase 1) - "business
    reporting" has no distinct code today.
  - reporting.usage: granted to every plan per the matrix ("Usage
    visibility: Yes" across Starter/Business/Pro/Scale) - seeded for
    completeness; wiring it would be a no-op gate since nobody is ever
    denied.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '245b5ab62c15'
down_revision: Union[str, None] = '710c98f42bfc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# key -> set of plan_codes that are granted this key (False for every
# other seeded plan_code). free_trial and enterprise get no rows either
# way, same deny-by-default posture as Phase 1.
_KEY_GRANTS = {
    'team.members.enabled': {'business', 'pro', 'scale'},
    'routing.business_hours': {'business', 'pro', 'scale'},
    'routing.team': {'business', 'pro', 'scale'},
    'routing.shared_handling': {'business', 'pro', 'scale'},
    'reporting.business': {'business', 'pro', 'scale'},
    'reporting.usage': {'starter', 'business', 'pro', 'scale'},
}
_ALL_PLANS = ['starter', 'business', 'pro', 'scale']


def upgrade() -> None:
    entitlements_table = sa.table(
        'plan_entitlements',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('plan_code', sa.String),
        sa.column('key', sa.String),
        sa.column('value_type', sa.String),
        sa.column('bool_value', sa.Boolean),
    )
    op.bulk_insert(
        entitlements_table,
        [
            {
                'id': str(uuid.uuid4()), 'plan_code': plan_code, 'key': key,
                'value_type': 'BOOLEAN', 'bool_value': plan_code in granted_plans,
            }
            for key, granted_plans in _KEY_GRANTS.items()
            for plan_code in _ALL_PLANS
        ],
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text("DELETE FROM plan_entitlements WHERE key = ANY(:keys)"),
        {"keys": list(_KEY_GRANTS.keys())},
    )
