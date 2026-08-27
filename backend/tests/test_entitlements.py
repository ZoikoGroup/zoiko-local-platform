"""ZL-COM-ENT-001 "Commercial Entitlement Governance" standard, Phase 1:
explicit entitlement keys gating real features by plan. Before this, these
keys' features were completely ungated by plan - any account on any plan
could create API keys, publish call flows, or pull analytics for free. See
app.billing.service.has_entitlement and app.core.deps.require_entitlement/
require_entitlement_for_api_key. AI Receptionist's own base-plan-or-addon
gate is separate - see billing.service.is_ai_receptionist_enabled_for_account,
covered in test_voice.py.
"""

from app.billing import service
from app.billing.models import SubscriptionStatus
from app.numbering.identity.models import Account, AccountType


def _make_account(db_session, name: str) -> Account:
    account = Account(name=name, account_type=AccountType.INDIVIDUAL)
    db_session.add(account)
    db_session.flush()
    return account


def _synced_paid_subscription(db_session, name: str, plan_code: str = "starter"):
    account = _make_account(db_session, name)
    service.get_or_create_subscription(db_session, account.id)  # free_trial, synced via conftest's autouse stub
    sub = service.change_plan(db_session, account.id, plan_code, actor="test-actor")
    return account, sub


def test_has_entitlement_denies_by_default_with_no_row(db_session):
    """ZL-COM-ENT-001's core principle: 'No entitlement record means no
    runtime access.' A free_trial account has zero seeded PlanEntitlement
    rows - every key must deny, not error or implicitly grant."""
    account = _make_account(db_session, "Entitlement Free Trial Co")
    service.get_or_create_subscription(db_session, account.id)

    assert service.has_entitlement(db_session, account.id, "routing.advanced") is False
    assert service.has_entitlement_scope(db_session, account.id, "developer.api.scope", min_scope="limited") is False


def test_has_entitlement_true_for_a_plan_that_grants_the_key(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Entitlement Pro Co", "pro")
    assert service.has_entitlement(db_session, account.id, "routing.advanced") is True
    assert service.has_entitlement(db_session, account.id, "reporting.advanced") is True
    # Pro is seeded "standard" - at or above the "limited" floor Business+ gets.
    assert service.has_entitlement_scope(db_session, account.id, "developer.api.scope", min_scope="limited") is True


def test_has_entitlement_false_for_a_plan_that_does_not_grant_the_key(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Entitlement Starter Co", "starter")
    assert service.has_entitlement(db_session, account.id, "routing.advanced") is False
    # Starter is seeded "none" - has_entitlement_scope must deny it, not
    # treat a real-but-lowest-rung string value as truthy.
    assert service.has_entitlement_scope(db_session, account.id, "developer.api.scope", min_scope="limited") is False


def test_has_entitlement_denies_for_a_canceled_subscription_regardless_of_plan_code(db_session):
    """A CANCELED subscription must never have any entitlement, even
    though its plan_code row still says 'pro' and would otherwise grant
    every key checked here."""
    account, sub = _synced_paid_subscription(db_session, "Entitlement Canceled Co", "pro")
    sub.status = SubscriptionStatus.CANCELED
    db_session.commit()

    assert service.has_entitlement(db_session, account.id, "routing.advanced") is False
    assert service.has_entitlement_scope(db_session, account.id, "developer.api.scope", min_scope="limited") is False


def test_assert_entitlement_raises_with_key_and_plan_code(db_session):
    account, _sub = _synced_paid_subscription(db_session, "Assert Entitlement Co", "starter")
    try:
        service.assert_entitlement(db_session, account.id, "routing.advanced")
        assert False, "expected EntitlementRequiredError"
    except service.EntitlementRequiredError as e:
        assert e.key == "routing.advanced"
        assert e.plan_code == "starter"


def test_get_entitlement_value_returns_the_real_value_not_just_a_bool(db_session):
    """ZL-COM-ENT-001 v3.0 - quantity/enum keys need their actual value,
    not just yes/no (has_entitlement's job)."""
    account, _sub = _synced_paid_subscription(db_session, "Entitlement Value Co", "pro")
    assert service.get_entitlement_value(db_session, account.id, "developer.api.scope") == "standard"
    assert service.get_entitlement_value(db_session, account.id, "routing.advanced") is True
    assert service.get_entitlement_value(db_session, account.id, "nonexistent.key") is None


def test_has_entitlement_scope_ladder_ordering(db_session):
    business, _ = _synced_paid_subscription(db_session, "Scope Ladder Business Co", "business")
    scale, _ = _synced_paid_subscription(db_session, "Scope Ladder Scale Co", "scale")
    # Business is seeded "limited" - meets its own floor but not "standard".
    assert service.has_entitlement_scope(db_session, business.id, "developer.api.scope", min_scope="limited") is True
    assert service.has_entitlement_scope(db_session, business.id, "developer.api.scope", min_scope="standard") is False
    # Scale is seeded "advanced" - clears every lower rung.
    assert service.has_entitlement_scope(db_session, scale.id, "developer.api.scope", min_scope="limited") is True
    assert service.has_entitlement_scope(db_session, scale.id, "developer.api.scope", min_scope="advanced") is True
    assert service.has_entitlement_scope(db_session, scale.id, "developer.api.scope", min_scope="contracted") is False


def test_get_entitlement_snapshot_includes_computed_ai_and_number_keys(db_session):
    """ai_receptionist.* and number.standard.included_qty are computed
    overlays, not stored plan_entitlements rows (see the seed migration's
    docstring) - the snapshot must still surface them alongside the real
    stored keys."""
    account, _sub = _synced_paid_subscription(db_session, "Snapshot Pro Co", "pro")
    snapshot = service.get_entitlement_snapshot(db_session, account.id)
    assert snapshot["routing.advanced"] is True
    assert snapshot["developer.api.scope"] == "standard"
    assert snapshot["ai_receptionist.enabled"] is True
    assert snapshot["ai_receptionist.included_minutes"] == 50
    assert snapshot["ai_receptionist.addon_minutes"] == 0
    # _synced_paid_subscription creates an Account with no User rows at
    # all, so the live seat count (get_included_number_ids' pool size) is
    # genuinely 0 here - covered with a real seat in test_billing.py's
    # signed-up-account paths instead of duplicating a User fixture here.
    assert snapshot["number.standard.included_qty"] == 0


def test_get_entitlement_snapshot_denies_for_free_trial(db_session):
    account = _make_account(db_session, "Snapshot Free Trial Co")
    service.get_or_create_subscription(db_session, account.id)
    snapshot = service.get_entitlement_snapshot(db_session, account.id)
    assert snapshot.get("routing.advanced") is None
    assert snapshot["ai_receptionist.included_minutes"] == 0
    assert snapshot["number.standard.included_qty"] == 0
