"""seed ZL-COM-ENT-001 v3.0 entitlement key register

Revision ID: fd300247929d
Revises: 2aca0e0b665f
Create Date: 2026-08-27 00:05:00.000000

ZL-COM-ENT-001 v3.0 Appendix A - the full ~34-key canonical register,
superseding Phase 1 (710c98f42bfc) / Phase 2 (245b5ab62c15) with the doc's
actual key names, per-plan values (booleans, integers, and now enum-style
strings via the STRING value_type added in 2aca0e0b665f), and coverage.
Values below are transcribed directly from the doc's §4 Master Plan
Allocation table and §5 detailed rules - starter/business/pro/scale only,
enterprise gets no rows (deny-by-default, matches existing posture, a
future contract-seeding pass handles Enterprise explicitly per-contract).

Three keys the doc lists (ai_receptionist.enabled/included_minutes/
addon_minutes) are deliberately NOT stored rows here - they're computed at
read time in billing.service.get_entitlement_snapshot from the existing,
already-correct Plan.included_ai_receptionist_minutes /
Subscription.ai_receptionist_addon_enabled / AiReceptionistAddonRate
sources (is_ai_receptionist_enabled_for_account /
_compute_ai_receptionist_overage), so there is exactly one source of truth
for AI Receptionist entitlement instead of a second, driftable copy in
this table. number.standard.included_qty is likewise computed (from live
seat count via get_included_number_ids), not stored - the doc's own "1 per
paid user" rule is already exactly what that function computes.

developer.api / developer.webhooks (plain booleans, Phase 1) are replaced
by developer.api.scope / developer.webhooks.scope (STRING scope ladder:
none/limited/standard/advanced/contracted) - the doc's own P0 finding is
that Business should get a real (if limited) API scope, which a flat
boolean can't express (Business previously got nothing).

team.members.enabled -> team.enabled and routing.shared_handling ->
routing.shared are renamed in place (UPDATE, not delete+insert) to match
the doc's exact Appendix A key names - same grant sets, no behavior
change, so existing plan_entitlements.id/created_at survive.

routing.team and reporting.usage (Phase 2, seeded for catalog completeness
only, zero code enforcement, and absent from v3.0's own Appendix A) are
retired. reporting.business IS in v3.0's Appendix A (re-seeded with real
per-plan values below) even though - like Phase 2 already noted - no
distinct "business reporting" feature module exists separately from
reporting.advanced in this codebase today; kept as real catalog data
rather than deleted, since the doc explicitly wants it registered.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fd300247929d'
down_revision: Union[str, None] = '2aca0e0b665f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALL_PLANS = ['starter', 'business', 'pro', 'scale']

# key -> {plan_code: value}. bool -> BOOLEAN row, int -> INTEGER row,
# str -> STRING row (inferred per-key from the Python type of its values).
_KEY_SPECS: dict[str, dict[str, bool | int | str]] = {
    # Numbers
    'number.additional.purchase_eligible': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'number.assignment.team': {'starter': False, 'business': True, 'pro': True, 'scale': True},
    # Voice
    'voice.app_to_app': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'voice.pstn.allowed': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'voice.pstn.rating_mode': {'starter': 'metered', 'business': 'metered', 'pro': 'metered', 'scale': 'metered'},
    # Voicemail
    'voicemail.enabled': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'voicemail.summary': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    # Routing
    'routing.forwarding': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'routing.business_hours': {'starter': False, 'business': True, 'pro': True, 'scale': True},
    'routing.transfer': {'starter': False, 'business': True, 'pro': True, 'scale': True},
    'routing.advanced': {'starter': False, 'business': False, 'pro': True, 'scale': True},
    'routing.multi_market': {'starter': False, 'business': False, 'pro': False, 'scale': True},
    # Messaging
    'messaging.enabled': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'messaging.shared_team': {'starter': False, 'business': True, 'pro': True, 'scale': True},
    # Team
    'team.roles.standard': {'starter': False, 'business': True, 'pro': True, 'scale': True},
    'admin.advanced_roles': {'starter': False, 'business': False, 'pro': False, 'scale': True},
    # Usage / reporting / analytics
    'usage.visibility.tier': {'starter': 'basic', 'business': 'standard', 'pro': 'advanced', 'scale': 'multi_market'},
    'reporting.business': {'starter': False, 'business': True, 'pro': True, 'scale': True},
    'reporting.advanced': {'starter': False, 'business': False, 'pro': True, 'scale': True},
    'analytics.cross_market': {'starter': False, 'business': False, 'pro': False, 'scale': True},
    # Developer
    'developer.api.scope': {'starter': 'none', 'business': 'limited', 'pro': 'standard', 'scale': 'advanced'},
    'developer.webhooks.scope': {'starter': 'none', 'business': 'limited', 'pro': 'standard', 'scale': 'advanced'},
    # Recording / transcription (orthogonal to the AI_PROCESSING consent gate)
    'recording.policy_enabled': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    'transcription.policy_enabled': {'starter': True, 'business': True, 'pro': True, 'scale': True},
    # Governance
    'audit.customer_visible.tier': {'starter': 'basic', 'business': 'standard', 'pro': 'advanced', 'scale': 'multi_market'},
    'compliance.workspace_controls.tier': {'starter': 'baseline', 'business': 'standard', 'pro': 'advanced', 'scale': 'multi_market'},
    'support.tier': {'starter': 'standard', 'business': 'standard', 'pro': 'priority', 'scale': 'priority_onboarding'},
    'admin.multi_market': {'starter': False, 'business': False, 'pro': False, 'scale': True},
    # Sema - placeholder only, no bundle system exists yet; never enforced.
    'sema.bundle_entitlement': {'starter': False, 'business': False, 'pro': False, 'scale': False},
}

# Retired: absent from v3.0's Appendix A, zero code enforcement (Phase 2).
_RETIRED_KEYS = ['routing.team', 'reporting.usage']

# Superseded by the .scope STRING keys above (Phase 1 booleans).
_SUPERSEDED_KEYS = ['developer.api', 'developer.webhooks']

# Now computed at read time (billing.service.get_entitlement_snapshot)
# instead of stored - see module docstring. Deleting the old Phase 1 row.
_MOVED_TO_COMPUTED_KEYS = ['ai_receptionist.enabled']

_RENAMES = {
    'team.members.enabled': 'team.enabled',
    'routing.shared_handling': 'routing.shared',
}


def _value_row(plan_code: str, key: str, value: bool | int | str) -> dict:
    row = {
        'id': str(uuid.uuid4()), 'plan_code': plan_code, 'key': key,
        'value_type': None, 'bool_value': None, 'int_value': None, 'string_value': None,
    }
    if isinstance(value, bool):
        row['value_type'], row['bool_value'] = 'BOOLEAN', value
    elif isinstance(value, int):
        row['value_type'], row['int_value'] = 'INTEGER', value
    else:
        row['value_type'], row['string_value'] = 'STRING', value
    return row


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        # Three of _KEY_SPECS' keys (routing.advanced, reporting.advanced,
        # routing.business_hours) already have rows from Phase 1/2 with
        # identical values - deleting+reinserting all of _KEY_SPECS keeps
        # this migration idempotent/simple rather than special-casing the
        # three that happen to be unchanged.
        sa.text("DELETE FROM plan_entitlements WHERE key = ANY(:keys)"),
        {"keys": _RETIRED_KEYS + _SUPERSEDED_KEYS + _MOVED_TO_COMPUTED_KEYS + list(_KEY_SPECS.keys())},
    )
    for old_key, new_key in _RENAMES.items():
        conn.execute(
            sa.text("UPDATE plan_entitlements SET key = :new_key WHERE key = :old_key"),
            {"new_key": new_key, "old_key": old_key},
        )

    entitlements_table = sa.table(
        'plan_entitlements',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('plan_code', sa.String),
        sa.column('key', sa.String),
        sa.column('value_type', sa.String),
        sa.column('bool_value', sa.Boolean),
        sa.column('int_value', sa.Integer),
        sa.column('string_value', sa.String),
    )
    op.bulk_insert(
        entitlements_table,
        [
            _value_row(plan_code, key, values[plan_code])
            for key, values in _KEY_SPECS.items()
            for plan_code in _ALL_PLANS
        ],
    )


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        sa.text("DELETE FROM plan_entitlements WHERE key = ANY(:keys)"),
        {"keys": list(_KEY_SPECS.keys())},
    )
    for old_key, new_key in _RENAMES.items():
        conn.execute(
            sa.text("UPDATE plan_entitlements SET key = :old_key WHERE key = :new_key"),
            {"new_key": new_key, "old_key": old_key},
        )

    entitlements_table = sa.table(
        'plan_entitlements',
        sa.column('id', sa.UUID(as_uuid=False)),
        sa.column('plan_code', sa.String),
        sa.column('key', sa.String),
        sa.column('value_type', sa.String),
        sa.column('bool_value', sa.Boolean),
    )
    _restored_boolean_grants = {
        'developer.api': {'pro', 'scale'},
        'developer.webhooks': {'pro', 'scale'},
        'ai_receptionist.enabled': {'pro', 'scale'},
        'routing.team': {'business', 'pro', 'scale'},
        'reporting.usage': {'starter', 'business', 'pro', 'scale'},
    }
    op.bulk_insert(
        entitlements_table,
        [
            {
                'id': str(uuid.uuid4()), 'plan_code': plan_code, 'key': key,
                'value_type': 'BOOLEAN', 'bool_value': plan_code in granted_plans,
            }
            for key, granted_plans in _restored_boolean_grants.items()
            for plan_code in _ALL_PLANS
        ],
    )
